"""§3 的 ``decide``，以及决策后端在能力表上的那一格（Phase F）。

## 两半放在一个文件里，因为它们测的是同一件事的两个面

前半是**工具**：一次判定怎么被发起、它的输入里缺了什么、回执上有什么。
后半是**能力表**：``get_capabilities`` 的 ``integrations`` 里 ``jev`` / ``laya``
那两行怎么说自己。合起来的那个主题是「这个部署的决策能力是**什么**」——
把两半拆开，会出现一处「表说没接线、工具却在跑」的改动两边都测不出来。

两半的驱动方式不同，而那是各自该有的方式：

* 工具那一半经 ``tests/mcp_harness.py`` 起**真的**服务端（真的角色判定、真的
  参数校验），因为「谁能调、什么参数算合法」正是这一层承重的东西。
* 表那一半读的是 ``_integrations`` 的返回值。它没有第二个调用点，而经服务面
  读它只会多一层 SDK 的措辞。

## 后端全部是替身，判据全部是「谁被调了、以什么模式、拿到什么」

这里**没有**跑过任何真实判定。``laya`` 的权重不在本机、vault 里没有
``TYPESAFE_API_KEY``，所以两个真后端今天的 ``ready`` 都如实是假
（``tests/test_decision_providers.py`` 量的是它们各自那一侧）。本文件量的是
**调用点**：题面从哪来、模式是不是恒定的、校验在不在推理之前、表有没有读对对象。

## 每条「不该发生」旁边都有一条「该发生」

「后端零调用」「消息里没有那句话」这类断言有一个廉价的反面：把被测功能整个删掉，
它们同样成立。所以 ``_Backend.calls`` 既是不该发生那几条的断言对象，也是该发生
那几条的正控载体 —— 每一条「没有调用」旁边都有一条同形的调用证明它在正常工作。

## 一处刻意**直接调私有函数**的地方

``_answer_view`` 的 ``YES_NO`` 分支经服务面到达不了：``compile_specs`` 编译出来的
三份题面**都是** ``CHOICE``（``application/decision_specs.py`` 里那道题是闭集选择题）。
写一条经服务面的用例去够它，得到的是一个恒真的断言。所以那一条直接调 ``_answer_view``，
并在它的 docstring 里写明这件事 —— 一个够不着的分支没有执行器，而「是否题不得被折成
标签」恰恰是 §4 最容易被顺手做掉的一格。
"""

from __future__ import annotations

import ast
import inspect
import json
import shutil
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest
import yaml

from marketops.adapters.mcp_server.decisions import (
    MAX_EVIDENCE_REFS,
    MAX_STATE_CHARS,
    _answer_view,
    decision_handlers,
)
from marketops.adapters.mcp_server.tools import _decision_rows
from marketops.adapters.profile_loader import load_profile
from marketops.adapters.sqlite.decisions import SqliteDecisionRecordStore
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.application.decision_specs import compile_specs
from mcp.server.mcpserver.exceptions import ToolError

from marketops.config import Settings
from marketops.domain.enums import (
    DecisionMode,
    DecisionOutcome,
    DecisionTask,
    JobInputKind,
    QuestionKind,
)
from marketops.domain.errors import ConfigError
from marketops.domain.profile import ProfileBundle
from marketops.ports.decisions import (
    ChoiceAnswer,
    DecisionCase,
    DecisionPreprocessing,
    DecisionProvider,
    DecisionRecord,
    DecisionSpec,
    DecisionUsage,
    YesNoAnswer,
    record_id,
)
from marketops.security.catalog import Role
from marketops.security.tokens import TokenRing, mint
from tests.conftest import DEFAULT_PROFILE, PROFILES_DIR
from tests.fakes import synthetic_dossiers, synthetic_scene_files
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload
from tests.test_evidence_store_contract import make_evidence

REPO_ROOT = Path(__file__).resolve().parents[1]
DECISIONS_MODULE = REPO_ROOT / "marketops" / "adapters" / "mcp_server" / "decisions.py"

#: 回执的键集，逐字对应 ``docs/MCP_SERVICE.md`` §4 那一句列举。**一条不多、一条
#: 不少** —— 少一个字段是把一条可审计的记录降级成一句结论，多一个字段是一次没有
#: 写下来的协议变更。
#:
#: 后四个不属于 §4 那句列举，它们属于 §2 的**落盘**：``work_item_id`` /
#: ``subject_ref`` 是这条记录挂在谁身上，``recorded_at`` 是它什么时候落下来的，
#: ``replayed`` 是**这一次调用**有没有问过后端。前三个是记录的一部分（它们与
#: ``decided_at`` 是两个不同的事实：那是**后端**什么时候判的），第四个不是 ——
#: 所以一次重放与一次新判定在记录上逐键相同，只在这一格上不同。
#:
#: ``source_method`` 是**第二个**刻意加上的键，而且它不是重构：§B16 关闭条件 7
#: 要求 Jev 的调用能反查「因计划里的哪一步被选择」，那一格必须有读者 —— 它就是
#: 读者。它同样落盘（与 ``replayed`` 相反），所以重放读回来的那一行照样带着它。
#: 这一行改动同时是 ``docs/MCP_SERVICE.md`` §4 那份清单的一次增补。
_RECEIPT_KEYS = {
    "record_id",
    "case_id",
    "spec_id",
    "spec_version",
    "input_sha256",
    "provider",
    "mode",
    "outcome",
    "answered",
    "answers",
    "reason",
    "model",
    "checkpoint",
    "preprocessing",
    "usage",
    "latency_ms",
    "decided_at",
    "evidence_refs",
    "note",
    "source_method",
    "work_item_id",
    "subject_ref",
    "recorded_at",
    "replayed",
}

#: 这套夹具的工作单元指的是谁。刻意不是一个 ``kent-*``：真实候选池里的 id 不该
#: 出现在一个只测工具面的文件里 —— 那会让「这条判据在测真账号」看起来像真的。
ACCOUNT = "decision-fixture-001"

#: 三种「没有调用路径」的判据各自**必须**含有的那句话。合成一句会让运维去接线一个
#: 已经接好的东西：第 2 种的处置是加一个环境变量，第 3 种的处置是别动它。
_NO_BACKEND = "本实例没有构造决策后端"
_NO_ADVISORY = "没有指定辅助后端"
_OTHER_ADVISORY = "decide 只咨询它"


# ----------------------------------------------------------------------
# 替身
# ----------------------------------------------------------------------
class _Backend(DecisionProvider):
    """一个只活在测试里的决策后端。

    ``plan`` 拿 case 返回**选项 key**（作答）或一个 ``DecisionOutcome``（答不上来）。
    两种返回值而不是一个哨兵值，理由与 ``tests/test_shadow.py::_Stub`` 相同：那两种
    结局的处置是反的。

    它记录自己被以什么模式调用 —— 「模式不是参数」那条判据要读这个。而 ``calls``
    同时是下面每一条「后端零调用」的断言对象。

    ``report_mode`` / ``report_provider`` 是「后端在回执里自称的模式 / 提供商」，
    缺省是调用方传下来的那个 / 它自己的名字。给一个别的值就是造一个**撒谎的后端**
    —— 真实 adapter 都取 ``self.name`` 并原样透传，所以那两个都不需要通过改
    adapter 来测。
    """

    def __init__(
        self,
        name: str,
        plan: Any = None,
        *,
        configured: bool = True,
        ready: bool = True,
        blocker: str = "",
        report_mode: DecisionMode | None = None,
        report_provider: str | None = None,
    ) -> None:
        self.name = name
        self._plan = plan if plan is not None else _first_option
        self._configured = configured
        self._ready = ready
        self._blocker = blocker
        self._report_mode = report_mode
        self._report_provider = report_provider
        self.calls: list[tuple[DecisionCase, DecisionMode]] = []

    @property
    def configured(self) -> bool:
        return self._configured

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def blocker(self) -> str:
        return self._blocker

    @property
    def cases(self) -> list[DecisionCase]:
        return [case for case, _ in self.calls]

    @property
    def modes(self) -> list[DecisionMode]:
        return [mode for _, mode in self.calls]

    def decide(self, case: DecisionCase, *, mode: DecisionMode) -> DecisionRecord:
        self.calls.append((case, mode))
        reported = self._report_mode if self._report_mode is not None else mode
        provider = self._report_provider if self._report_provider is not None else self.name
        planned = self._plan(case)
        if isinstance(planned, DecisionOutcome):
            return _record(provider, case, outcome=planned, mode=reported)
        return _record(provider, case, choice=planned, mode=reported)


def _first_option(case: DecisionCase) -> str:
    """没指定计划时的缺省：闭集里的第一项。"""
    question = case.spec.questions[0]
    return case.spec.choices_by_question[question.question_id][0]


def _cannot(outcome: DecisionOutcome):
    def plan(case: DecisionCase) -> DecisionOutcome:
        return outcome

    return plan


def _record(
    provider: str,
    case: DecisionCase,
    choice: str | None = None,
    *,
    outcome: DecisionOutcome = DecisionOutcome.ANSWERED,
    mode: DecisionMode,
) -> DecisionRecord:
    """一条回执。``mode`` 由**调用方传下来的那个**填 —— 回执里的模式必须是这次
    调用真的用了的模式，一个由替身自己挑一个的模式会让「工具恒用 assist」这条
    判据变成在测替身。"""
    if outcome is DecisionOutcome.ANSWERED:
        assert choice is not None
        question = case.spec.questions[0]
        answers: tuple[Any, ...] = (
            ChoiceAnswer(
                question_id=question.question_id,
                choice=choice,
                distribution=None,
                confidence=None,
            ),
        )
        reason = ""
    else:
        answers = ()
        reason = f"替身：{outcome.value}"
    decided_at = "2026-09-22T00:00:00Z"
    return DecisionRecord(
        record_id=record_id(case, provider=provider, decided_at=decided_at),
        case_id=case.case_id,
        spec_id=case.spec.spec_id,
        spec_version=case.spec.version,
        input_sha256=case.input_sha256,
        provider=provider,
        model="stub-model",
        checkpoint="stub-checkpoint",
        preprocessing=DecisionPreprocessing(
            language="en", route=f"{provider}-替身路线", input_chars=len(case.state)
        ),
        outcome=outcome,
        answers=answers,
        reason=reason,
        latency_ms=1,
        usage=DecisionUsage(input_tokens=None, output_tokens=None, billed=False),
        decided_at=decided_at,
        mode=mode,
        evidence_refs=case.evidence_refs,
    )


# ----------------------------------------------------------------------
# 夹具
# ----------------------------------------------------------------------
@pytest.fixture
def page_triage(bundle: ProfileBundle) -> DecisionSpec:
    """profile 编译出来的那道闭集选择题。题面是**服务端**的事，这个夹具只是把它
    取出来当断言的参照，不是一条通往 ``decide`` 的输入。"""
    return compile_specs(bundle)[DecisionTask.PAGE_TRIAGE]


@pytest.fixture
def work_item(settings: Settings) -> str:
    """一件**真的**工作单元，主语是 :data:`ACCOUNT`。

    判定记录只能挂在一件真的工作单元上（§2：无主的记录是「通用模型记忆库」的
    第一块砖），所以 ``decide`` 的每一次合法调用都要带一个真的 id。造它的过程
    是「受理一个作业、把候选身份编译成工作单元」，那是
    ``tests/test_decision_store.py`` 的主题，这里只需要它的结果。

    编译用的 Scene 是**盘上这一刻那一份**（``load_profile(settings).scene_files``），
    不是 ``synthetic_scene_files()``：判定那条路现在按**钉住的那一版字节**编译题面
    （``tools.pinned_bundle``），而那批合成字节不是一份 profile，装载会失败。
    「钉住的就是盘上这一份」也是真实路径上唯一会出现的形状 —— 受理与编译读的是同一次
    ``load_profile``；两份**分家**的形状由下面那几条判据专门摆出来。
    """
    store = SqliteJobStore(settings.database_path)
    job = store.admit(
        scene_id=settings.profile,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref="decision-tools-fixture",
        idempotency_key="decision-tools-v1",
        request_sha256="0" * 64,
    )
    items = store.compile_work_items(
        job.job_id, synthetic_dossiers(ACCOUNT), scene=load_profile(settings).scene_files
    )
    return items[0].work_item_id


def _put_in_scope_evidence(settings: Settings, *evidence_ids: str) -> None:
    """把几条**真的存在、且属于这件工作**的证据写进库。

    只有 ``decide`` 会因为**引用范围**而拒的那几条用例需要它。范围校验之后，
    一条引用的合法性不再只取决于「它是不是规范 id」，所以那些用例的正控必须
    让被引的那几行真的在库里 —— 否则正控会以一个恒拒的实现的形式「通过」。
    """
    store = SqliteEvidenceStore(settings.database_path)
    for evidence_id in evidence_ids:
        store.put_evidence(make_evidence(evidence_id=evidence_id, account_id=ACCOUNT))


@pytest.fixture
def operator(settings: Settings, tmp_path: Path):
    """一个同时持 controller 与 researcher 的主体 —— ``decide`` 只对后者可见，
    ``get_capabilities`` 只对前者可见，而这两件事下面都要调。

    返回的 ``call`` 每次构造一份**新的**处理器表与**新的**服务端：``decide`` 与
    ``get_capabilities`` 必须看到同一批对象（``_decision_rows`` 按对象同一性回答
    「decide 会调用谁」），所以这两个参数是同一次调用的两个实参，不是两个夹具。
    """
    return _operator(settings, tmp_path)


def _operator(settings: Settings, tmp_path: Path):
    """上面那个夹具的实现体。

    抽出来是因为有一条判据要在一个**临时 profile 目录**上的 ``Settings`` 上开一个
    同样的会话（``test_an_in_flight_work_item_decides_on_its_pinned_profile``）——
    而那一条不能走 ``settings`` 夹具，它读的是仓库里那份真的 profile 目录，改不了。
    两份实现（夹具一份、那一条自己再搭一份）会在接线漂移时分叉：那时红的是一条
    与被测行为无关的用例。
    """
    token, digest = mint("claude-ops", [Role.CONTROLLER, Role.RESEARCHER])
    ring_path = tmp_path / "tokens.json"
    ring_path.write_text(
        json.dumps(ring_payload(ring_entry(digest, "claude-ops", ["controller", "researcher"]))),
        encoding="utf-8",
    )
    ring = TokenRing.from_file(ring_path)

    def call(
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        decisions: dict[str, DecisionProvider] | None = None,
        advisory: DecisionProvider | None = None,
        wire_tool: bool = True,
    ):
        """``wire_tool=False`` 造一个**只接控制面**的实例（合法的一种部署）。

        那时 ``decide`` 不在处理器表里，于是 ``wired`` 为空 —— 而「这个实例没装这个
        工具」是四种「没有调用路径」里的第一种，另外三种都在工具装了的前提下。
        """
        outcome = drive_with_settings(
            settings=settings,
            tokens=ring,
            token=token,
            research=decision_handlers(
                settings=settings,
                advisory=advisory,
                # 落点用**工厂**，与组合根在 ``wire_decisions`` 里建的是同一个
                # 东西。没有它 ``decide`` 整次拒绝（§2：一条存不下来的判定不该
                # 先付一次费），而那样这个文件里每一条正常路径都会红 —— 那不是
                # 这些判据要测的东西。
                records=lambda: SqliteDecisionRecordStore(settings.database_path),
            )
            if wire_tool
            else None,
            decisions=decisions,
            advisory=advisory,
            calls=((tool, arguments or {}),),
            want_tool_list=False,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome.call(tool)

    return call


def _rows(result) -> dict[str, dict[str, Any]]:
    payload = result.as_json()
    assert payload is not None and "integrations" in payload, result.text
    return {row["name"]: row for row in payload["integrations"]}


def _decision_rows_of(rows: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {name: row for name, row in rows.items() if row["kind"] == "decision"}


# ----------------------------------------------------------------------
# decide —— 正常路径
# ----------------------------------------------------------------------
def test_decide_calls_the_backend_once_and_returns_the_whole_record(
    operator, work_item: str, page_triage: DecisionSpec
) -> None:
    """正控：正常路径下真的判了一次，回执里 §4 列举的每一样都在。

    没有这一条，下面每一条「后端零调用」都可能是被一个整个坏掉的工具满足的。
    """
    backend = _Backend("jev")
    result = operator(
        "decide",
        {"work_item_id": work_item, "spec_id": page_triage.spec_id, "state": "A club in Devon, England."},
        decisions={"jev": backend},
        advisory=backend,
    )
    assert not result.is_error, result.text
    payload = result.as_json()

    assert set(payload) == _RECEIPT_KEYS, "回执的形状变了 —— 那是协议变更，不是重构"
    assert backend.modes == [DecisionMode.ASSIST], "工具必须以 assist 调用后端"
    assert len(backend.cases) == 1

    case = backend.cases[0]
    assert case.spec == page_triage, "判的必须是从 profile 编译出来的那份题面"
    assert case.state == "A club in Devon, England."
    assert payload["provider"] == "jev"
    assert payload["mode"] == DecisionMode.ASSIST.value
    assert payload["outcome"] == DecisionOutcome.ANSWERED.value
    assert payload["answered"] is True
    assert payload["reason"] == ""
    assert payload["spec_id"] == page_triage.spec_id
    assert payload["spec_version"] == page_triage.version
    assert payload["input_sha256"] == case.input_sha256
    assert payload["evidence_refs"] == []
    assert payload["preprocessing"]["input_chars"] == len(case.state)

    # 回答按题面的 kind 投影：选择题有 choice，没有 probability。
    question = page_triage.questions[0]
    assert payload["answers"] == [
        {
            "question_id": question.question_id,
            "kind": QuestionKind.CHOICE.value,
            "choice": page_triage.choices_by_question[question.question_id][0],
            "distribution": None,
            "confidence": None,
        }
    ]


def test_the_case_id_is_derived_from_the_input_and_not_from_the_clock(
    operator, work_item: str, page_triage: DecisionSpec
) -> None:
    """``case_id`` 由**输入本身**派生，所以同一次判定的重试不会分叉成两个 case。

    判据是**恒等式**，不是「两次调用相等」：后者对一个秒级精度的 ``utc_now()``
    会碰巧成立。恒等式同时杀掉两个方向的改动 —— 固定 case_id（会让两次不同的
    输入共用一个身份，而 ``record_id`` 里含它）与 ``utc_now()``。
    """
    backend = _Backend("jev")

    def decide(state: str) -> dict[str, Any]:
        result = operator(
            "decide",
            {"work_item_id": work_item, "spec_id": page_triage.spec_id, "state": state},
            decisions={"jev": backend},
            advisory=backend,
        )
        assert not result.is_error, result.text
        return result.as_json()

    first = decide("A club in Devon.")
    second = decide("A club in Devon.")
    third = decide("A different club in Devon.")

    assert first["case_id"] == f"{page_triage.spec_id}#{first['input_sha256'][:16]}"
    assert first["case_id"] == second["case_id"]
    assert third["case_id"] != first["case_id"], "换了输入必须换一个身份"
    assert third["input_sha256"] != first["input_sha256"]


# ----------------------------------------------------------------------
# decide —— 重放不重复计费
# ----------------------------------------------------------------------
def test_a_replayed_judgement_is_not_paid_for_twice(
    operator, work_item: str, page_triage: DecisionSpec, settings: Settings
) -> None:
    """**§B14 的判据之一**：同一个问题问第二遍**不碰后端**，而换一份输入就该再付一次。

    计费发生在**后端调用**上（``DecisionUsage.billed`` 是后端自报的那一格），所以
    「不重复计费」的可判形态就是「后端只被调了一次」。``record_id`` 含 ``decided_at``，
    内容寻址挡的是「同一件事记成两条」，**挡不住**重复付费 —— 挡住第二次的是写之前
    按 ``(work item, provider, case)`` 查的那一次。

    负控与正控一样承重，而且方向是**相反**的：一条把重放做成无条件的实现（不管
    输入变没变都返回已有那条），会让「换一份输入」也读不到新判定 —— 那比多付一次费
    更坏，因为它是**把没发生过的事记成了发生过**。所以第三条调用必须真的再付一次费。
    """
    backend = _Backend("jev")

    def decide(state: str) -> dict[str, Any]:
        result = operator(
            "decide",
            {"work_item_id": work_item, "spec_id": page_triage.spec_id, "state": state},
            decisions={"jev": backend},
            advisory=backend,
        )
        assert not result.is_error, result.text
        return result.as_json()

    # 三次调用**逐次断言**，不先把三次都发出去：``backend.calls`` 是累计的，
    # 三次都跑完再数长度的话，「第二次没碰后端」测的是「后端总共只被调了两次」——
    # 而那条在第三次真的该调用后端时同样成立。
    first = decide("A club in Devon, England.")
    assert first["replayed"] is False, "第一次调用不是一次重放"
    assert len(backend.calls) == 1, "第一次调用必须真的判了一次（否则下面那条是空的）"

    replayed = decide("A club in Devon, England.")
    assert replayed["replayed"] is True, (
        "第二次调用没有标出「这是重放」—— 读的人会以为这是又判了一次"
    )
    assert replayed["record_id"] == first["record_id"]
    assert replayed["decided_at"] == first["decided_at"]
    assert len(backend.calls) == 1, "同一个问题问第二遍又调用了一次后端 —— 那是又付了一次费"

    fresh = decide("A different club in Devon, England.")
    assert fresh["replayed"] is False, "换了一份输入却读回了上一条判定"
    assert fresh["record_id"] != first["record_id"]
    assert len(backend.calls) == 2

    # 台账里那一件工作下只有**两条**记录：重放既没有多写一行，也没有改写第一行。
    stored = SqliteDecisionRecordStore(settings.database_path)
    assert stored.decision_for_case(work_item, "jev", first["case_id"]) is not None
    assert stored.decision_for_case(work_item, "jev", fresh["case_id"]) is not None
    assert first["case_id"] != fresh["case_id"]


# ----------------------------------------------------------------------
# decide —— 没有 mode 旋钮
# ----------------------------------------------------------------------
def test_the_mode_is_not_a_parameter_even_though_the_port_demands_one(
    settings: Settings,
) -> None:
    """工具面**没有** ``mode`` 参数，而端口上那个是必填的。

    两半都要。只看工具面，「没有 mode」与「模式还没实现」分不开；只看端口，
    「端口要求显式模式」这条纪律没有一个执行点去证明它真的被执行了。

    §4 要求影子的答案对研究侧隐藏。一个 ``mode="shadow"`` 的参数就是一条让调用方
    把影子答案要回去的入口，而用它的那一次在记录上与 assist 完全同形。

    ``source_method`` 在签名里而 ``mode`` 不在，这两件事不是同一个问题：模式决定
    **这次判定算哪一种**（可见性随它变），而步骤只是这次调用的来路标注。前者是
    一条能把影子答案要回去的入口，后者是一个可空的锚点（§B16 关闭条件 7）。
    """
    handler = decision_handlers(settings=settings, advisory=None)["decide"]
    parameters = set(inspect.signature(handler).parameters)
    assert parameters == {
        "spec_id",
        "work_item_id",
        "state",
        "evidence_refs",
        "source_method",
    }, "题面与工作单元都以 **id** 的形式进来，模式则根本没有位置可放"
    assert "mode" not in parameters, (
        "``mode`` 一旦成为一个参数，调用方就能把影子答案要回去 —— 而用它的那一次"
        "在记录上与 assist 完全同形"
    )

    # 正控之一：端口上那个 ``mode`` 确实存在、且**没有缺省值**。
    mode = inspect.signature(DecisionProvider.decide).parameters["mode"]
    assert mode.kind is inspect.Parameter.KEYWORD_ONLY
    assert mode.default is inspect.Parameter.empty, (
        "一个有缺省值的 mode 会让「忘了写模式」变成一条静悄悄进了轨迹的影子记录"
    )
    # 正控之二：题面确实在端口那一侧（``DecisionCase`` 带着它），所以「工具面只收
    # 一个 id」不是因为没有别的东西可传。
    assert {field.name for field in fields(DecisionCase)} >= {"spec", "state", "case_id"}


def test_the_receipt_says_assist_and_that_no_automated_acceptance_exists(
    operator, work_item: str, page_triage: DecisionSpec
) -> None:
    """回执自称 assist，并明说自己**不是** shadow baseline、也不是自动验收。

    ``DecisionMode`` 的闭集是这条判据的硬那一半：本构建没有自动验收模式，
    「assist 就是通行证」这件事必须不可能被读出来。
    """
    assert {mode.value for mode in DecisionMode} == {"shadow", "assist"}, (
        "多一个模式是一个产品决定 —— 它要连带改这条判据，而不是悄悄出现"
    )

    backend = _Backend("jev")
    result = operator(
        "decide",
        {"work_item_id": work_item, "spec_id": page_triage.spec_id, "state": "A club."},
        decisions={"jev": backend},
        advisory=backend,
    )
    payload = result.as_json()
    assert payload["mode"] == "assist"
    assert "shadow" not in payload["mode"]
    note = payload["note"]
    assert "assist" in note
    assert "**不是** shadow baseline" in note, "回执必须自己说清它是哪一条路径"
    assert "automated" in note, "「assist 等于通行证」这件事必须不可能被读出来"


def test_a_backend_that_reports_another_mode_is_refused(
    operator, work_item: str, page_triage: DecisionSpec
) -> None:
    """后端回了一条**自称别的模式**的记录时整次拒绝 —— 那是 §4 唯一会被破坏的方式。

    回执如实写着模式，所以这不是一次数据损坏：被破坏的是可见性。一条 shadow 记录
    交给研究侧，就是把「影子答案不进研究轨迹」这条规矩的执行，交给调用方去读一个
    它没理由读的字段；而一条别的模式会以「assisted」之外的标记进轨迹。两者在回执上
    都看起来完全正常 —— 所以判据只能在这一层。

    正控是另一半：同一个替身把模式如实报回来时，回执照常给出。

    两次调用**必须落在两个不同的 case 上**：幂等键是 (work item, provider, case)，
    同一个后端拿同一份输入问第二遍是一次合法的重放，拿回的会是第一条记录 —— 那
    时候这个撒谎的后端根本不会被调到。所以第二次换一段文本。
    """
    honest = _Backend("jev", report_mode=DecisionMode.ASSIST)
    good = operator(
        "decide",
        {"work_item_id": work_item, "spec_id": page_triage.spec_id, "state": "A club."},
        decisions={"jev": honest},
        advisory=honest,
    )
    assert not good.is_error, good.text
    assert good.as_json()["mode"] == "assist"

    lying = _Backend("jev", report_mode=DecisionMode.SHADOW)
    result = operator(
        "decide",
        {
            "work_item_id": work_item,
            "spec_id": page_triage.spec_id,
            "state": "A club that gets a different case.",
        },
        decisions={"jev": lying},
        advisory=lying,
    )
    assert result.is_error
    assert "shadow" in result.text and "assist" in result.text
    assert "record_id" not in result.text, "被拒的记录一个字段都不许交出去"


def test_a_backend_that_reports_another_provider_is_refused_and_writes_nothing(
    operator, work_item: str, page_triage: DecisionSpec
) -> None:
    """后端回了一条**自称别的 provider** 的记录时整次拒绝，而且一行都不落。

    这一格与上一条不同：模式的谎话该由调用方读那个字段来防，而 **provider 是幂等键
    的一段**。这个工具在**调用之前**只有 ``advisory.name`` 可查（那时还没有记录），
    所以两个名字若能不同，查的时候用一个、写的时候用另一个 —— 重放查不中，照旧再
    付一次费。两个真实实现都取 ``self.name``，所以这不是在防手滑，是在防一段**可以
    被后端自报改写的查询键**。

    判据的承重的一半是**什么都没写下去**：把撒谎的那次换回诚实后端、拿同一份输入
    再问一遍，必须是一次**新判定**（``replayed`` 为 false）而不是一次重放。留了一行
    的话，那条记录会挂在一个查不中的键上，而「同一份输入问第二次」从此每次都再付
    一次费 —— 记录在库里、账也在涨，两边都看起来正常。

    这次拒绝**发生在付费之后**：那个后端已经被调过一次了。它挡的是那行记录，不是
    这一次调用 —— 诚实地说出这一点，比让防守看起来比它实际更强要好。
    """
    lying = _Backend("jev", report_provider="laya")
    refused = operator(
        "decide",
        {"work_item_id": work_item, "spec_id": page_triage.spec_id, "state": "A club."},
        decisions={"jev": lying},
        advisory=lying,
    )
    assert refused.is_error
    assert "laya" in refused.text and "jev" in refused.text
    assert "record_id" not in refused.text, "被拒的记录一个字段都不许交出去"
    assert len(lying.calls) == 1, "判定已经发生，也就已经付过费 —— 拒的是那行记录"

    honest = _Backend("jev")
    again = operator(
        "decide",
        {"work_item_id": work_item, "spec_id": page_triage.spec_id, "state": "A club."},
        decisions={"jev": honest},
        advisory=honest,
    )
    assert not again.is_error, again.text
    assert again.as_json()["replayed"] is False, (
        "被拒的那一行不许留在库里 —— 留着它，幂等查询就会漏掉每一次重放"
    )
    assert len(honest.calls) == 1


# ----------------------------------------------------------------------
# decide —— 题面从哪来
# ----------------------------------------------------------------------
def test_an_unknown_spec_id_is_refused_and_the_known_ones_are_named(
    operator, work_item: str, bundle: ProfileBundle, page_triage: DecisionSpec
) -> None:
    """未知 ``spec_id`` 整次拒绝，并列出**本 Scene 真的有**的那几个。

    绝不「用一个默认题面兜着」：那样两次记录上的 ``spec_version`` 会不同而调用方
    毫不知情 —— 题面一改，旧标签就静默不可比了，而报告上只显示「后端不一致」。
    """
    known = sorted(spec.spec_id for spec in compile_specs(bundle).values())
    assert len(known) == 3, "这条用例的前提是这份 profile 声明了三个 task"
    assert page_triage.spec_id in known

    backend = _Backend("jev")

    # 空 id 比「找不到的 id」更早被拒：它不是一次查找失败，是一次缺失的参数。
    blank = operator(
        "decide",
        {"work_item_id": work_item, "spec_id": "  ", "state": "A club."},
        decisions={"jev": backend},
        advisory=backend,
    )
    assert blank.is_error
    assert "spec_id 不能为空" in blank.text
    assert "没有 spec_id" not in blank.text

    result = operator(
        "decide",
        {
            "work_item_id": work_item,
            "spec_id": f"{bundle.profile_id}:no-such-task",
            "state": "A club.",
        },
        decisions={"jev": backend},
        advisory=backend,
    )
    assert result.is_error
    assert "没有 spec_id" in result.text
    for spec_id in known:
        assert spec_id in result.text, f"回执没说出它认识 {spec_id!r}"
    assert backend.calls == [], "被拒的调用不许碰后端"


def test_a_spec_sent_as_an_argument_is_ignored_not_accepted(
    operator, work_item: str, page_triage: DecisionSpec
) -> None:
    """题面**只能**以 id 的形式进来：随参数送一份题面，它既不生效也不报错。

    实测的行为是 SDK 把声明之外的参数**丢掉**（不是拒绝），所以这条的判据不能是
    「它报错」—— 那会是一条关于 SDK 版本的断言。真正的判据是：判的那份题面仍然是
    profile 编译出来的那一份，送进去的那份连一个字段都没落进 case。

    这比「它会拒绝」更重要：拒绝只说明这一次没成功，而忽略说明**这个参数不存在**。
    """
    backend = _Backend("jev")
    result = operator(
        "decide",
        {
            "work_item_id": work_item,
            "spec_id": page_triage.spec_id,
            "state": "A club.",
            "spec": {"spec_id": "attacker:made-up", "questions": [{"question_id": "band"}]},
            "mode": "shadow",
        },
        decisions={"jev": backend},
        advisory=backend,
    )
    assert not result.is_error, result.text
    assert len(backend.cases) == 1
    assert backend.cases[0].spec == page_triage
    assert backend.cases[0].spec.spec_id == page_triage.spec_id
    assert backend.modes == [DecisionMode.ASSIST], "送一个 mode 进来没有用 —— 那正是重点"
    assert result.as_json()["spec_version"] == page_triage.version


# ----------------------------------------------------------------------
# decide —— 三种「这次判定不会发生」
# ----------------------------------------------------------------------
def test_no_advisory_backend_is_refused_before_anything_else(
    operator, work_item: str, page_triage: DecisionSpec
) -> None:
    """没指定辅助后端时，**在任何别的动作之前**拒绝，并说清那不是一个判定结果。

    输入刻意同时是坏的（未知 spec_id、超长 state）：回执里出现哪一句，就能看出
    校验的顺序。这一条之所以要单独列，是因为「本部署没指定后端」的处置是**加一个
    环境变量**，而「判过了、后端弃权」的处置是换输入或换后端 —— 压成一句，运维会
    去查一个根本没被调用过的后端。
    """
    result = operator(
        "decide",
        {
            "work_item_id": work_item,
            "spec_id": "no-such-spec",
            "state": "x" * (MAX_STATE_CHARS + 1),
        },
        decisions=None,
        advisory=None,
    )
    assert result.is_error
    assert _NO_ADVISORY in result.text
    assert "MARKETOPS_DECISION_PROVIDER" in result.text, "要说出那个能修好它的变量"
    assert "没有 spec_id" not in result.text, "顺序反了：它先去读了 profile"
    assert "state 超过" not in result.text, "顺序反了：它先去量了长度"

    # 正控：同一份坏输入，接上后端之后先撞上**长度**那一关，而把长度改短之后才
    # 轮到题面那一关。两句都出现，说明上面那句「没指定后端」不是一句恒答的话：
    # 校验的顺序真的由配置决定。
    backend = _Backend("jev")
    wired = {
        "work_item_id": work_item,
        "spec_id": "no-such-spec",
        "state": "x" * (MAX_STATE_CHARS + 1),
    }
    too_long = operator(
        "decide", wired, decisions={"jev": backend}, advisory=backend
    )
    assert too_long.is_error
    assert "state 超过" in too_long.text
    assert _NO_ADVISORY not in too_long.text

    unknown = operator(
        "decide",
        {"work_item_id": work_item, "spec_id": "no-such-spec", "state": "A club."},
        decisions={"jev": backend},
        advisory=backend,
    )
    assert unknown.is_error
    assert "没有 spec_id" in unknown.text
    assert backend.calls == [], "三次被拒的调用一次都不许碰后端"


def test_an_empty_state_is_refused_before_the_backend_is_touched(
    operator, work_item: str, page_triage: DecisionSpec
) -> None:
    """空 ``state`` 不是「没有可判的东西」，是「调用方忘了带输入」。"""
    backend = _Backend("jev")
    for empty in ("", "   ", "\n\t "):
        result = operator(
            "decide",
            {"work_item_id": work_item, "spec_id": page_triage.spec_id, "state": empty},
            decisions={"jev": backend},
            advisory=backend,
        )
        assert result.is_error, repr(empty)
        assert "state 不能为空" in result.text
    assert backend.calls == []


def test_a_state_over_the_ceiling_is_refused_and_never_truncated(
    operator, work_item: str, page_triage: DecisionSpec
) -> None:
    """超长 ``state`` 整次拒绝，**不裁剪**（§4：No silent truncation）。

    裁掉之后后端判的是另一段文本，而那条记录看起来完全正常 —— 这正是
    ``DecisionPreprocessing`` 里刻意没有 ``truncated`` 字段的理由。

    上界那一条是正控：刚好 ``MAX_STATE_CHARS`` 必须过，而且后端拿到的
    ``input_chars`` 必须等于全长。只测「超长被拒」，一个恒拒的实现同样通过。
    """
    backend = _Backend("jev")

    too_long = "x" * (MAX_STATE_CHARS + 1)
    refused = operator(
        "decide",
        {"work_item_id": work_item, "spec_id": page_triage.spec_id, "state": too_long},
        decisions={"jev": backend},
        advisory=backend,
    )
    assert refused.is_error
    assert str(MAX_STATE_CHARS) in refused.text and str(len(too_long)) in refused.text
    assert "不截断" in refused.text
    assert backend.calls == [], "一次注定被拒的调用不许先发一次付费推理"

    at_the_ceiling = "x" * MAX_STATE_CHARS
    accepted = operator(
        "decide",
        {"work_item_id": work_item, "spec_id": page_triage.spec_id, "state": at_the_ceiling},
        decisions={"jev": backend},
        advisory=backend,
    )
    assert not accepted.is_error, accepted.text
    assert [len(case.state) for case in backend.cases] == [MAX_STATE_CHARS]
    assert accepted.as_json()["preprocessing"]["input_chars"] == MAX_STATE_CHARS


# ----------------------------------------------------------------------
# decide —— 证据引用
# ----------------------------------------------------------------------
def test_a_non_canonical_evidence_ref_is_refused_before_any_spend(
    operator, settings: Settings, work_item: str, page_triage: DecisionSpec
) -> None:
    """每条引用都必须**已经是**一个规范 id。

    理由不是洁癖：服务面的 instructions 对调用方承诺「任何结论都应当能追到一条
    已抓取的公开文档」，而这些引用就是那条承诺在记录里的落点。收下一个解不开的
    引用，会让那句承诺变成一句无法验证的话 —— 而它在记录上看起来与一条真的引用
    一模一样。

    规范 id 只是引用合法的一半。另一半 —— 那几行**真的存在**、且属于这件工作 ——
    由 ``tests/test_decision_store.py`` 那三条用例量。所以这里的正控要先把被引的
    两行写进库，否则它测的是一条恒拒的实现。
    """
    backend = _Backend("jev")

    def attempt(refs: Any):
        return operator(
            "decide",
            {
                "work_item_id": work_item,
                "spec_id": page_triage.spec_id,
                "state": "A club.",
                "evidence_refs": refs,
            },
            decisions={"jev": backend},
            advisory=backend,
        )

    # 一条 URL：`:` 与 `/` 都不是规范 id 的字符。从 URL 直接派生引用是最自然的
    # 写法，所以它必须是被拒的那一种。
    url = attempt(["https://example.com/evidence/1"])
    assert url.is_error
    assert "不是规范形式" in url.text

    # 条数上限：引用逐字进记录，所以条数上限就是这条记录的大小上限。
    many = attempt([f"evidence-{index:03d}" for index in range(MAX_EVIDENCE_REFS + 1)])
    assert many.is_error
    assert str(MAX_EVIDENCE_REFS) in many.text

    assert backend.calls == [], "被拒的调用不许碰后端"

    # 正控：合法引用真的进了记录，而且真的动了输入指纹。
    _put_in_scope_evidence(settings, "evidence-001", "evidence-002")
    good = attempt(["evidence-001", "evidence-002"])
    assert not good.is_error, good.text
    payload = good.as_json()
    assert payload["evidence_refs"] == ["evidence-001", "evidence-002"]
    plain = operator(
        "decide",
        {"work_item_id": work_item, "spec_id": page_triage.spec_id, "state": "A club."},
        decisions={"jev": backend},
        advisory=backend,
    )
    assert plain.as_json()["input_sha256"] != payload["input_sha256"]


# ----------------------------------------------------------------------
# decide —— 「这一步服务计划里的哪一步」（§B16 关闭条件 7）
# ----------------------------------------------------------------------
#: 这一份 Scene 的声明**真的开出来**的那一步。它必须是**两个**条件都满足的字面量：
#: 在 ``domain/sources.py::SOURCE_METHODS`` 里注册过，**而且**有一份声明给它入口
#: （``search.query_templates`` 开 ``search.engine.v1``）。
DECIDED_STEP = "search.engine.v1"

#: 注册过、**却没有任何声明给它入口**的那一步。它的存在是这一对判据的可判别性：
#: 只查闭集的实现会把它当合法步骤收下，而条件 7 要的是「属于这一轮的计划」。
UNPLANNED_STEP = "research.skill.v1"


def test_the_step_this_plan_opened_is_echoed_on_the_decision_record(
    operator, work_item: str, page_triage: DecisionSpec
) -> None:
    """正控：服务端交出的那一步被收下，而且**留在记录上**。

    留下的意思是它落盘 —— 所以 ``get_decision`` 读回来的同一行也带着它。只回显
    不落盘的话，「这条判定服务的是哪一步」这件事只活在那一次调用的返回值里，
    而条件 7 要的反查是**事后**做的。

    留空是另一条路（这次判定不属于任何步骤），它由下面那条的反面覆盖：空串不是
    缺省，所以「没给」与「给了一个真的」在回执上是两句不同的话。
    """
    backend = _Backend("jev")
    result = operator(
        "decide",
        {
            "work_item_id": work_item,
            "spec_id": page_triage.spec_id,
            "state": "A club in Devon, England.",
            "source_method": DECIDED_STEP,
        },
        decisions={"jev": backend},
        advisory=backend,
    )
    assert not result.is_error, result.text
    payload = result.as_json()

    assert payload["source_method"] == DECIDED_STEP
    assert len(backend.calls) == 1, "合法的步骤不该改变这次调用做不做"

    read = operator("get_decision", {"record_id": payload["record_id"]})
    assert not read.is_error, read.text
    assert read.as_json()["source_method"] == DECIDED_STEP, (
        "步骤没有落盘 —— 反查要在一段独立的会话里问得出同一个答案"
    )

    # 没给这一格：回执说 ``None``（没有声称），不是 ``""``、更不是一个默认步骤。
    plain = operator(
        "decide",
        {"work_item_id": work_item, "spec_id": page_triage.spec_id, "state": "A club."},
        decisions={"jev": backend},
        advisory=backend,
    )
    assert plain.as_json()["source_method"] is None


def test_a_step_this_plan_never_opened_is_refused_before_the_backend_is_paid(
    operator, work_item: str, page_triage: DecisionSpec
) -> None:
    """反例：一个**注册过、但这一份声明没给它入口**的步骤 ⇒ 拒绝，且**后端零调用**。

    这是 ``test_a_non_canonical_evidence_ref_is_refused_before_any_spend`` 的同一条
    纪律换一格：Jev 是**付过费**的，所以一次注定被拒的判定不该先把钱花掉。
    ``backend.calls == []`` 是这条判据的主语 —— 断言「工具报了个错」会让一个先判定
    后校验的实现照样绿。

    为什么必须是这个 id 而不是一个拼错的名字：拼错的名字连闭集都过不了，那条路
    在加这一格之前就已经是红的了。``UNPLANNED_STEP`` 是**闭集里的成员**，所以它
    专门证伪「校验只查注册表、不查这一轮的计划」—— 而后者正是缺口 B。
    """
    backend = _Backend("jev")
    result = operator(
        "decide",
        {
            "work_item_id": work_item,
            "spec_id": page_triage.spec_id,
            "state": "A club in Devon, England.",
            "source_method": UNPLANNED_STEP,
        },
        decisions={"jev": backend},
        advisory=backend,
    )

    assert result.is_error, "计划里没有这一步，判定却还是发生了"
    assert "计划" in result.text, result.text
    assert backend.calls == [], "被拒的调用碰了后端 —— 那一次是要付钱的"

    # 而且什么都没落下来。判据不是「查一个不存在的 id」，而是**同一个案子再判
    # 一次不是重放** —— 被拒的那一次若在库里留了行，这一次会命中幂等、直接返回
    # 那一行（``replayed`` 为 true），而它看起来与一次正常的判定完全一样。
    later = operator(
        "decide",
        {
            "work_item_id": work_item,
            "spec_id": page_triage.spec_id,
            "state": "A club in Devon, England.",
        },
        decisions={"jev": backend},
        advisory=backend,
    )
    assert later.as_json()["replayed"] is False, (
        "被拒的那一次留下了记录 —— 幂等键命中了它"
    )


# ----------------------------------------------------------------------
# decide —— 三种结局分得开
# ----------------------------------------------------------------------
def test_abstained_and_unavailable_are_two_different_failures(
    operator, work_item: str, page_triage: DecisionSpec
) -> None:
    """弃权与不可用都「没有答案」，而处置是反的，所以它们必须在回执上分得开。

    §4 逐字：缺凭据 / 缺 checkpoint 是一个 blocker，不是一次伪造的成功推理。
    压成一个布尔之后，「这个 Scene 缺一个决策后端」与「这份输入两个后端都不肯答」
    在运维界面上不可区分。

    两次调用刻意**不是同一份输入**：幂等键是 (work item, provider, case)，同一个
    后端拿同一份输入问第二遍是一次合法的重放，拿回的是第一条记录 —— 那时候第二次
    的结局根本没被判过，而这一条会以一个「两次都一样」的假象通过。
    """
    assert {item.value for item in DecisionOutcome} == {
        "answered",
        "abstained",
        "unavailable",
    }

    def receipt(outcome: DecisionOutcome, state: str) -> dict[str, Any]:
        backend = _Backend("jev", _cannot(outcome))
        result = operator(
            "decide",
            {"work_item_id": work_item, "spec_id": page_triage.spec_id, "state": state},
            decisions={"jev": backend},
            advisory=backend,
        )
        assert not result.is_error, result.text
        return result.as_json()

    abstained = receipt(DecisionOutcome.ABSTAINED, "A club the backend will not answer for.")
    unavailable = receipt(DecisionOutcome.UNAVAILABLE, "A club with no checkpoint behind it.")

    for payload in (abstained, unavailable):
        assert payload["answered"] is False
        assert payload["answers"] == []
        assert payload["reason"].strip(), "没有理由的弃权与一次崩溃在报告上无法区分"
        assert _NO_ADVISORY not in payload["reason"], (
            "「后端答不上来」与「本部署没接后端」必须是两句话"
        )
    assert abstained["outcome"] != unavailable["outcome"]
    assert abstained["reason"] != unavailable["reason"]


# ----------------------------------------------------------------------
# decide —— 回答的投影
# ----------------------------------------------------------------------
def test_a_yes_no_answer_is_a_probability_and_carries_no_label() -> None:
    """是否题只回概率，**没有标签** —— 折成 yes/no 需要一个人没授权过的阈值。

    这里**直接调私有函数**：``compile_specs`` 编译出来的三份题面都是 ``CHOICE``，
    所以这个分支经服务面到达不了。够不着的分支没有执行器，而「顺手把 0.7 变成
    yes」恰好是最容易发生的一次改动。
    """
    answer = _answer_view(YesNoAnswer(question_id="band", probability=0.7, confidence=None))
    assert set(answer) == {"question_id", "kind", "probability", "confidence"}
    assert answer["kind"] == QuestionKind.YES_NO.value
    assert answer["probability"] == 0.7
    assert "choice" not in answer and "label" not in answer

    # 正控：同一个投影对选择题给出**另一个**形状 —— 一条恒真的实现不能同时满足两边。
    choice = _answer_view(
        ChoiceAnswer(
            question_id="band",
            choice="core",
            distribution=(("core", 0.7), ("news", 0.3)),
            confidence=None,
        )
    )
    assert set(choice) == {"question_id", "kind", "choice", "distribution", "confidence"}
    assert "probability" not in choice
    assert choice["distribution"] == [
        {"key": "core", "probability": 0.7},
        {"key": "news", "probability": 0.3},
    ]

    # 端口加了第三个成员而这里没跟上时，回执里会**少一条回答** —— 那读起来与
    # 「后端只答了这些」完全一样，所以这里必须拒绝而不是跳过。
    with pytest.raises(ToolError) as caught:
        _answer_view("a question id, not an answer")  # type: ignore[arg-type]
    assert "不认识的回答类型" in str(caught.value)


# ----------------------------------------------------------------------
# 分层：决策工具不 import 任何具体后端
# ----------------------------------------------------------------------
def test_the_decision_tool_reaches_no_concrete_backend() -> None:
    """``decisions.py`` 只认注入进来的那**一个**后端对象。

    import 一个具体 adapter 就等于「判给谁」在模块里被决定了 —— 而它是一次部署
    的参数（``MARKETOPS_DECISION_PROVIDER``）。import ``interfaces/`` 则是分层反向
    （那是组合根的方向）。

    两个扫描器各管一半：AST 看不见 ``importlib.import_module("…")``，而按字符串扫
    看不见 ``from … import``。所以第二种按**点分路径**扫，而不是按厂商名 —— 后端
    的名字（jev / laya）在给运维读的报错文本里本来就该出现。
    """
    modules = _imported_modules(DECISIONS_MODULE)
    assert modules, "扫描器没读到任何 import —— 这条判据会假绿"
    assert [name for name in modules if "interfaces" in name] == []
    assert [name for name in modules if "adapters" in name] == []
    assert [name for name in modules if "jev" in name or "laya" in name] == []

    needles = ("marketops.adapters.", "adapters.jev", "adapters.laya", "interfaces.")
    assert [needle for needle in needles if _hits(DECISIONS_MODULE, needle)] == []

    # 正控：同一个扫描器必须看得见这个文件**确实**import 的两样东西。
    assert any("ports.decisions" in name for name in modules)
    assert any(name == "application.decision_specs" for name in modules)


def test_the_backend_name_scanner_actually_fires(tmp_path: Path) -> None:
    """正对照：两种写法都要被抓到，干净的文件不许被误报。"""
    path = tmp_path / "tool.py"
    path.write_text("from ...adapters.jev import JevDecisionProvider\n", encoding="utf-8")
    assert "adapters.jev" in _imported_modules(path)
    path.write_text("X = \"marketops.adapters.laya\"\n", encoding="utf-8")
    assert _hits(path, "marketops.adapters.")
    path.write_text("import json\n\nX = 1\n", encoding="utf-8")
    assert _imported_modules(path) == ["json"]
    assert not _hits(path, "marketops.adapters.")


def _imported_modules(path: Path) -> list[str]:
    """文件里 import 的模块名。相对 import 记的是 ``node.module``（不带前导点），
    所以 ``from ...adapters.jev import X`` 读作 ``adapters.jev``。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = [
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ]
    found += [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    return found


def _hits(path: Path, needle: str) -> bool:
    """文件里**代码用到的**字符串字面量中有没有这个针（docstring 不算散文里的）。

    与 ``tests/test_shadow.py::_hits`` 同一个做法：一句解释性的散文不是接线。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    return any(
        needle in node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    )


# ----------------------------------------------------------------------
# 能力表 —— 决策那两行
# ----------------------------------------------------------------------
def test_the_row_reads_the_backend_and_does_not_hardcode_configured(operator) -> None:
    """``configured`` / ``runtime_ready`` / ``blocker`` 逐格取自 provider 自己。

    这一格曾经是写死的 ``configured: False``。写死的问题不是它当时不对，而是它
    **不会变**：接线之后那两行照旧报「尚未接线」，于是「哪一天该把它改过来」变成
    一件要靠人记得的事。

    两种替身的取值刻意都**不是**常量会有的那一种，而且**互不相同**：一个
    ``configured`` 真而 ``ready`` 假（本机今天就是这个形状 —— SDK 装了、钥匙没配），
    另一个反过来。一个把任一格写死的实现，只可能蒙对一个。
    """
    shapes = (
        (True, False, "替身：缺 TYPESAFE_API_KEY"),
        (False, True, ""),
    )
    for configured, ready, blocker in shapes:
        backend = _Backend("jev", configured=configured, ready=ready, blocker=blocker)
        rows = _decision_rows_of(
            _rows(operator("get_capabilities", decisions={"jev": backend}, advisory=backend))
        )

        assert sorted(rows) == ["jev"]
        row = rows["jev"]
        assert row["kind"] == "decision"
        assert row["phase"] == "F"
        assert row["configured"] is configured
        assert row["runtime_ready"] is ready
        assert row["blocker"] == blocker, (
            "blocker 必须逐字来自后端自己 —— 它已经脱过敏，"
            "在这里重写一遍会把脱敏这件事变成两份"
        )
        assert _NO_BACKEND not in row["blocker"]


def test_only_the_advisory_backend_is_wired(operator) -> None:
    """``wired`` 在这一族上是**逐后端**的：``decide`` 只咨询一个后端。

    给两行都写 ``True`` 等于说「两个后端都会被调用」，而它同时是错的、且是这张表
    最容易被误读的一格（读成「两个都能用」）。

    两个方向都跑：把 advisory 换成另一个，两个格子的真假必须跟着换。一条恒真的
    ``wired`` 只能满足其中一次。
    """
    first = _Backend("jev", configured=False, ready=False, blocker="替身：没有钥匙")
    second = _Backend("laya", configured=True, ready=True)

    for advisory, other in ((first, second), (second, first)):
        table = {"jev": first, "laya": second}
        rows = _decision_rows_of(
            _rows(operator("get_capabilities", decisions=table, advisory=advisory))
        )
        assert rows[advisory.name]["wired"] is True
        assert rows[other.name]["wired"] is False
        assert rows[advisory.name]["blocker"] == advisory.blocker, (
            "被调用的后端身上不该挂着「为什么不会被调用」"
        )
        assert rows[other.name]["blocker"].startswith(other.blocker)
        assert repr(advisory.name) in rows[other.name]["blocker"]


def test_the_three_ways_of_having_no_call_path_are_three_sentences(operator) -> None:
    """「没有调用路径」是**三件事**，各说各的。

    1. 这个实例压根没装那个工具（只接控制面的部署）。
    2. 后端构造了、工具也装了，但本部署**没指定**辅助后端 —— 处置是加一个环境变量。
    3. 它**是别的**后端 —— 处置是别动它。

    合成一句「未接线」会让运维去接线一个已经接好的东西，或者去查一个根本没被
    调用过的后端。第 2 与第 3 的区别尤其要留住：一句 `if not wired` 会把「加个
    环境变量」和「什么都别改」说成同一件事。
    """
    first = _Backend("jev", configured=True, ready=True)
    second = _Backend("laya", configured=True, ready=True)
    table = {"jev": first, "laya": second}

    control_only = _decision_rows_of(
        _rows(operator("get_capabilities", decisions=table, advisory=first, wire_tool=False))
    )
    assert all(row["wired"] is False for row in control_only.values())
    assert all("decide 未接线" in row["blocker"] for row in control_only.values())

    no_advisory = _decision_rows_of(
        _rows(operator("get_capabilities", decisions=table))
    )
    assert all(row["wired"] is False for row in no_advisory.values())
    assert all(
        "MARKETOPS_DECISION_PROVIDER" in row["blocker"] for row in no_advisory.values()
    )

    other = _decision_rows_of(
        _rows(operator("get_capabilities", decisions=table, advisory=first))
    )
    assert other["jev"]["wired"] is True
    assert _OTHER_ADVISORY in other["laya"]["blocker"]
    assert "MARKETOPS_DECISION_PROVIDER" not in other["laya"]["blocker"], (
        "「本部署选了别人」与「本部署没选」是两件事"
    )
    assert "decide 未接线" not in other["laya"]["blocker"]

    sentences = [
        control_only["jev"]["blocker"],
        no_advisory["jev"]["blocker"],
        other["laya"]["blocker"],
    ]
    assert len(set(sentences)) == 3, "三句话各不相同才算分得开"


def test_a_table_without_any_backend_is_a_fourth_sentence(operator) -> None:
    """一个后端都没构造是**另一个**轴上的事，不是上面三种里的任何一种。

    它的落点不是 ``_why_not_called``（工具装了，只是没有东西可咨询），而是
    ``_decision_rows`` 自己：表上仍然要有那两行，因为少一行读出来是「它不在表上」，
    而不是「它没就绪」。
    """
    not_built = _decision_rows_of(_rows(operator("get_capabilities")))
    assert sorted(not_built) == ["jev", "laya"]
    assert all(row["configured"] is False for row in not_built.values())
    assert all(row["runtime_ready"] is False for row in not_built.values())
    assert all(row["wired"] is False for row in not_built.values())
    assert all(_NO_BACKEND in row["blocker"] for row in not_built.values())
    assert all("MARKETOPS_DECISION_PROVIDER" not in row["blocker"] for row in not_built.values())


def test_a_table_that_lost_its_advisory_is_refused() -> None:
    """表按对象同一性回答「decide 会调用谁」，所以它必须拒绝一份**说谎**的组合。

    这不是一次手滑的防护，而是一个「表会说谎」的状态：``advisory`` 不在这份表里
    时，每一行都会报 ``wired: false`` —— 而 ``decide`` 照样会调它。那时「表说没有
    调用路径」与「调用路径正在跑」同时成立，且没有任何一处看得出来。
    """
    backend = _Backend("jev")
    wired = _decision_rows({"jev": backend}, frozenset({"decide"}), backend)
    assert [row["wired"] for row in wired] == [True]

    with pytest.raises(ConfigError) as caught:
        _decision_rows({"laya": _Backend("laya")}, frozenset({"decide"}), backend)
    assert "不在这份 decisions 里" in str(caught.value)
    assert "laya" in str(caught.value), "要说清表里到底有什么"

    with pytest.raises(ConfigError) as missing:
        _decision_rows(None, frozenset({"decide"}), backend)
    assert "没有传决策后端" in str(missing.value)


def test_the_names_come_from_the_deployment_and_not_from_the_constant() -> None:
    """有后端时行的名字取自 ``provider.name``；只有一行时表上就只有一行。

    ``_DECISION_NAMES`` 只在**一个后端都没构造**时用来答「本该有哪几个」——
    它是那句话的载体，不是这张表的形状。
    """
    built = _decision_rows({"jev": _Backend("jev")}, frozenset({"decide"}), None)
    assert [row["name"] for row in built] == ["jev"]

    without = _decision_rows(None, frozenset({"decide"}), None)
    assert [row["name"] for row in without] == ["jev", "laya"]
    assert all(row["detail"] == without[0]["detail"] for row in without), (
        "detail 说的是这一族的形状，两个后端共用一句是对的"
    )


# ----------------------------------------------------------------------
# §6：在飞的那件按**钉住的那一版**答题
# ----------------------------------------------------------------------
def _a_profile_we_can_edit(tmp_path: Path) -> Settings:
    """一份**改得动**的 profile 目录 + 指向它的 ``Settings``。

    本文件其余的用例走 ``settings`` 夹具，它读的是仓库里那份**真的** profile 目录
    —— 判据改不了它，也不该改。这一条要的正是「盘上那一份被改过」，所以复制一份
    到 ``tmp_path``。逐字节复制（``shutil.copytree``）而不是 YAML 往返：这一条要
    比的是**版本**，而重新序列化过的 YAML 已经不是同样的字节了。
    """
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PROFILES_DIR / DEFAULT_PROFILE, profiles_dir / DEFAULT_PROFILE)

    resolved = Settings(
        data_root=tmp_path / "runtime", profiles_dir=profiles_dir, profile=DEFAULT_PROFILE
    )
    resolved.ensure_data_dirs()
    return resolved


def _reword_a_page_triage_option(settings: Settings) -> None:
    """把 ``decisions.page_triage`` 里一条选项的**描述**改掉 —— 一次真的改盘。

    改**描述**而不是改 key、也不是删掉一条：

    * 描述进 ``DecisionSpec.version``（``decision_specs._version`` 把每条选项的
      ``(key, description)`` 算进指纹），所以改它会得到一个**不同的题面版本**。
    * 闭集不变，所以装载与编译都照常成功。这一条要的是「盘上换了一份**好**规则」，
      而不是「盘上那份坏了」—— 后者是另一条判据（装载失败该怎么处置）。

    取值用 ``sorted(...)[0]`` 而不是写死一个 key：**测试里不出现市场词**，键名改了
    就该是这条用例跟着动，而不是它读出一个 AttributeError。
    """
    path = settings.profiles_dir / settings.profile / "profile.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    options = document["decisions"]["page_triage"]["options"]
    key = sorted(options)[0]
    options[key] = "A reworded description: the profile changed after this job was compiled."
    path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _a_work_item_on(settings: Settings, *, key: str) -> str:
    """受理 + 编译一件工作单元，编译时钉住**盘上此刻**这一份 Scene。"""
    store = SqliteJobStore(settings.database_path)
    job = store.admit(
        scene_id=settings.profile,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref=key,
        idempotency_key=key,
        request_sha256="0" * 64,
    )
    items = store.compile_work_items(
        job.job_id, synthetic_dossiers(ACCOUNT), scene=load_profile(settings).scene_files
    )
    return items[0].work_item_id


def test_an_in_flight_work_item_decides_on_its_pinned_profile_not_on_disk(tmp_path) -> None:
    """§6：编译之后 profile 被改过，**在飞的那件仍按钉住的那一版答题**。

    这条判据是「``EffectiveResearchPlan`` 随 WorkItem 冻结：**中途 profile 更新不改
    正在跑的 attempt**」那句在判定这条路上的落点。它必须**真的改一次盘** —— 在一个
    两边逐字节相同的实现上，这条判据是空的（「按钉住的」与「按盘上的」指的是同一批
    字节，一个恒读 ``load_profile(settings)`` 的实现照样全绿）。所以下面每一步都
    先确认两份**确实不同**。

    两侧并排，缺一不可：

    * **在飞的那件没变**：盘上换了一份之后，它下一次判定报出的 ``spec_version``
      与改盘之前逐字相同。``decide`` 的 ``spec_version`` 是它**执行**的那份题面的
      指纹，所以这一格量的就是「执行的是哪一份」。
    * **新编译的那件变了**：改盘之后受理、编译的那件工作报出的是**新的**版本。

    只有第一条是空的：一个把题面当常量、从不读盘的实现也满足它。加上第二条之后，
    「按钉住的那一版跑」才与「按一个冻住的常量跑」分开 —— 而后者会让新受理的工作
    也按老的题面判。

    两次判定用的是**不同的 state**：``case_id`` 由输入派生，同一个 state 的第二次
    调用是**重放**（返回库里那一行，``spec_version`` 自然是老的），那样这一条测的
    是幂等而不是题面。
    """
    settings = _a_profile_we_can_edit(tmp_path)
    call = _operator(settings, tmp_path)
    backend = _Backend("jev")

    pinned_version = load_profile(settings).scene_version
    in_flight = _a_work_item_on(settings, key="before-the-edit")
    pinned_spec = compile_specs(load_profile(settings))[DecisionTask.PAGE_TRIAGE]

    def decide(work_item_id: str, state: str) -> dict[str, Any]:
        result = call(
            "decide",
            {"work_item_id": work_item_id, "spec_id": pinned_spec.spec_id, "state": state},
            decisions={"jev": backend},
            advisory=backend,
        )
        assert not result.is_error, result.text
        return result.as_json()

    before = decide(in_flight, "A club in Devon, England.")

    # **真的改盘**，而且改过之后的那一份与钉住的那一份**版本真的不同** —— 没有这一步，
    # 下面两条断言在任何实现上都成立。
    _reword_a_page_triage_option(settings)
    edited_version = load_profile(settings).scene_version
    assert edited_version != pinned_version, (
        "盘上那一份必须真的换成另一版，否则这条判据是空的"
    )
    edited_spec = compile_specs(load_profile(settings))[DecisionTask.PAGE_TRIAGE]
    assert edited_spec.spec_id == pinned_spec.spec_id, (
        "spec_id 是身份（哪一件判定），version 是内容（哪一版题面）—— 这条判据比的是"
        "内容，所以身份必须没变，否则「版本不同」可能只是换了一道题"
    )
    assert edited_spec.version != pinned_spec.version, (
        "新题面的指纹必须真的变了，否则「盘上那份改了」这件事没有可观测的后果"
    )

    # 一：在飞的那件**没变** —— 同一个 spec_id、同一个版本，尽管盘上已经不是它了。
    after = decide(in_flight, "A different club in Devon, England.")
    assert after["spec_id"] == before["spec_id"]
    assert after["spec_version"] == before["spec_version"] == pinned_spec.version
    assert after["spec_version"] != edited_spec.version, (
        "这就是这一条要挡的那件事：编译之后有人改了 profile，一件在飞的工作按**新"
        "题面**被判定，而记录上写着钉住的那一版"
    )

    # 二：改盘之后受理、编译的那件，用的是**新版** —— 「新 run 才用新版并给 impact」。
    fresh = _a_work_item_on(settings, key="after-the-edit")
    fresh_receipt = decide(fresh, "A third club in Devon, England.")
    assert fresh_receipt["spec_id"] == pinned_spec.spec_id
    assert fresh_receipt["spec_version"] == edited_spec.version
    assert fresh_receipt["spec_version"] != pinned_version


def test_a_work_item_whose_pinned_bytes_do_not_load_cannot_be_decided(
    settings, operator, page_triage: DecisionSpec
) -> None:
    """钉住的字节**在、但装载不出来** ⇒ 判定整次被拒，**不回落**到盘上那一份。

    这是上面那条判据的兄弟，量的却是**没有第三种回落**：钉住的那一批读不成一份
    profile 时，回落会让这次判定按**另一份**题面执行，而记录上写着钉住的那一版 ——
    那正是这一片要关掉的事，只是换了一个入口（``get_assignment`` 那一侧的同款判据
    在 ``tests/test_mcp_assignment_tools.py``）。

    造法用 ``synthetic_scene_files``：那批字节是**刻意的**、不成任何 profile 的
    字节（``tests/fakes.py`` 里写着理由），所以装载一定失败 —— 而不是「可能失败」。

    还要断**后端零调用**：这次拒绝发生在付费之前，否则「拒绝」与「付了费才发现
    判不了」在账上不可区分。
    """
    store = SqliteJobStore(settings.database_path)
    fake = synthetic_scene_files(tag="an-unloadable-pin")
    job = store.admit(
        scene_id=settings.profile,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref="unloadable-pin-fixture",
        idempotency_key="decision-tools-unloadable-pin",
        request_sha256="0" * 64,
    )
    item = store.compile_work_items(
        job.job_id, synthetic_dossiers(ACCOUNT), scene=fake
    )[0]
    backend = _Backend("jev")

    result = operator(
        "decide",
        {"work_item_id": item.work_item_id, "spec_id": page_triage.spec_id, "state": "A club in Devon."},
        decisions={"jev": backend},
        advisory=backend,
    )

    assert result.is_error
    # 说清是**哪一件**工作、钉的是**哪一版** —— 只说「装载失败」的话，处置的人
    # 不知道该去修哪一件工作的哪一批字节。
    assert item.work_item_id in result.text
    assert fake.version in result.text
    assert backend.calls == [], "这次拒绝必须发生在付费之前"

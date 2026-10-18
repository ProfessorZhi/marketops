"""影子：多臂跑同一批冻结样本，报告**只报分歧**。

本文件测三组东西，每一组对应一处会静默出错的地方：

1. **没作答的臂留在分组里。** 滤掉它们，2-of-3 会显示成全体一致，而那次「一致」
   里有一条从未发生的判定。四种结局（见 ``SampleVerdict``）各自有判据，因为把它们
   压成「有没有分歧」会同时丢掉两大类待办。
2. **参考标签只出计数，且分母在报告里看得见。** 没标签的样本、题面漂移过的样本
   都不进分母 —— 而它们各自单独列出。一个 ``accuracy`` 浮点会把三个分母藏起来。
3. **没有裁决。** ``as_dict()`` 的键集对着写死的外部清单断言，且一个扫「裁决词」的
   扫描器自带正对照 —— 否则「报告里没有 winner」这条判据，在一个把键全删光的
   ``as_dict`` 上也是绿的。
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from marketops.application.decision_specs import compile_spec
from marketops.application.shadow import (
    SHADOW_SAMPLES_SCHEMA,
    ArmSummary,
    FrozenSampleSet,
    SampleComparison,
    SampleVerdict,
    ShadowSample,
    load_samples,
    run_shadow,
)
from marketops.domain.enums import (
    DecisionMode,
    DecisionOutcome,
    DecisionTask,
    QuestionKind,
)
from marketops.domain.errors import ConfigError
from marketops.domain.profile import (
    DeclaredDecisions,
    DeclaredTaskSpec,
    ProfileBundle,
)
from marketops.ports.decisions import (
    ChoiceAnswer,
    DecisionCase,
    DecisionPreprocessing,
    DecisionProvider,
    DecisionQuestion,
    DecisionRecord,
    DecisionSpec,
    DecisionUsage,
    YesNoAnswer,
)

#: §9 Experiment 3 逐字点名的三族。**写死在这里**，不从 ``DecisionTask`` 抄 ——
#: 抄一遍的话，枚举少一个成员这条判据也跟着少一条，而那正是它要发现的事。
_SECTION_9_TASKS = ("page_triage", "responsibility_relation", "role_function")

#: 报告里**不许出现**的字段名。它们全都是「替人做裁决」的形状：多数表决、胜出者、
#: 共识、一致率。§4 要求 automated acceptance 在独立留出评估之前保持关闭，而多数
#: 表决就是一个没人证明过的判定。
_FORBIDDEN_REPORT_KEYS = ("majority", "winner", "consensus", "accuracy", "best", "agreement_rate")


def _task(name: str) -> DecisionTask:
    return DecisionTask(name)


# --- 后端替身 ---------------------------------------------------------------


def _says(choice: str):
    """一个恒答某个选项的臂。"""

    def plan(case: DecisionCase) -> str:
        return choice

    return plan


def _cannot(outcome: DecisionOutcome):
    """一个答不上来的臂。``reason`` 由 :func:`_record` 补 —— ``DecisionRecord``
    要求非 ANSWERED 的记录**必非空**理由，那是端口的硬要求，不是这里的风格。"""

    def plan(case: DecisionCase) -> DecisionOutcome:
        return outcome

    return plan


class _Stub(DecisionProvider):
    """一个只活在测试里的决策后端。

    ``plan`` 拿 case 返回**选项 key**（作答）或一个 ``DecisionOutcome``（答不上来
    —— 含 ``ABSTAINED`` 与 ``UNAVAILABLE``）。两种返回值而不是一个哨兵值：那两种
    结局的处置是反的，用一个 ``None`` 同时表示它们会让本文件自己犯下它要测的那个病。

    它**记录自己被以什么模式调用** —— 「模式不是参数」那条判据要读这个。
    """

    def __init__(self, name: str, plan=None) -> None:
        self.name = name
        self._plan = plan or _first_option
        self.calls: list[tuple[str, DecisionMode]] = []

    def decide(self, case: DecisionCase, *, mode: DecisionMode) -> DecisionRecord:
        self.calls.append((case.case_id, mode))
        planned = self._plan(case)
        if isinstance(planned, DecisionOutcome):
            return _record(self.name, case, outcome=planned, mode=mode)
        return _record(self.name, case, choice=planned, mode=mode)


def _first_option(case: DecisionCase) -> str:
    """没指定计划时的缺省：闭集里的第一项。"""
    question = case.spec.questions[0]
    return case.spec.choices_by_question[question.question_id][0]


def _record(
    provider: str,
    case: DecisionCase,
    choice: str | None = None,
    *,
    outcome: DecisionOutcome = DecisionOutcome.ANSWERED,
    answers: tuple[Any, ...] | None = None,
    reason: str = "",
    mode: DecisionMode = DecisionMode.SHADOW,
) -> DecisionRecord:
    if answers is None:
        if outcome is DecisionOutcome.ANSWERED:
            assert choice is not None
            answers = (
                ChoiceAnswer(
                    question_id=case.spec.questions[0].question_id,
                    choice=choice,
                    distribution=None,
                    confidence=None,
                ),
            )
        else:
            answers = ()
        if outcome is not DecisionOutcome.ANSWERED and not reason:
            reason = f"替身：{outcome.value}"
    return DecisionRecord(
        record_id=f"{provider}-{case.case_id}-{choice or outcome.value}",
        case_id=case.case_id,
        spec_id=case.spec.spec_id,
        spec_version=case.spec.version,
        input_sha256=case.input_sha256,
        provider=provider,
        model="stub-model",
        checkpoint="stub-checkpoint",
        preprocessing=DecisionPreprocessing(
            language="en", route="stub", input_chars=len(case.state)
        ),
        outcome=outcome,
        answers=answers,
        reason=reason,
        latency_ms=1,
        usage=DecisionUsage(input_tokens=None, output_tokens=None, billed=False),
        decided_at="2026-09-22T00:00:00Z",
        mode=mode,
        evidence_refs=case.evidence_refs,
    )


# --- 冻结样本的构造 ---------------------------------------------------------


def _sample(name: str, task: str, *, reference: str | None = None) -> ShadowSample:
    return ShadowSample(
        sample_id=name,
        task=_task(task),
        state=f"冻结的输入文本（{name}）",
        reference=reference,
    )


def _frozen(bundle: ProfileBundle, samples: tuple[ShadowSample, ...]) -> FrozenSampleSet:
    """按**当前** bundle 冻结一批样本。

    版本从 ``compile_spec`` 现取 —— 这是「没有漂移」那一侧的构造方式，而它单独一条
    判据不够（两边一起动就一起绿）。漂移那一侧在
    ``test_a_reworded_option_stops_the_label_comparison`` 里用的是**改过题面的
    bundle**，那一次不回声。
    """
    tasks = {sample.task for sample in samples}
    return FrozenSampleSet(
        samples=samples,
        spec_versions={task: compile_spec(bundle, task).version for task in tasks},
    )


def _reworded(bundle: ProfileBundle, task: DecisionTask, key: str) -> ProfileBundle:
    """把某个选项的描述改一个字 —— 一次**真的**题面变更。

    这是「题面换过之后旧标签不可比」那条判据的输入端。用真实的改法（改 profile 的
    声明）而不是 monkeypatch：``version`` 是内容寻址的，改 label 就是它防的那件事。
    """
    declared = bundle.decisions
    assert declared is not None, "这个 profile 必须声明过 decisions 才有得改"
    spec = declared.tasks[task]
    labels = dict(spec.labels)
    labels[key] = labels[key] + "（改过）"
    tasks = dict(declared.tasks)
    tasks[task] = DeclaredTaskSpec(instructions=spec.instructions, labels=labels)
    return dataclasses.replace(bundle, decisions=DeclaredDecisions(tasks=tasks))


# --- 模式不是参数 -----------------------------------------------------------


def test_run_shadow_offers_no_mode_knob() -> None:
    """``run_shadow`` **没有** ``mode`` 形参。

    做成旋钮，调用方就能把一次影子判定记成辅助判定 —— 而两者的可见性不同（辅助的
    答案进研究轨迹，影子的不进）。裁判是 ``DecisionProvider.decide`` **有**这个名字
    （且必填）：一个 sig 里根本没有 mode 的仓库不该在这里变绿。
    """
    assert "mode" not in inspect.signature(run_shadow).parameters
    assert "mode" in inspect.signature(DecisionProvider.decide).parameters


def test_every_arm_is_called_in_shadow_mode(bundle: ProfileBundle) -> None:
    left, right = _Stub("left"), _Stub("right", plan=_cannot(DecisionOutcome.ABSTAINED))
    run_shadow(
        _frozen(bundle, (_sample("s1", "page_triage"),)),
        [left, right],
        bundle,
    )
    assert [mode for _, mode in left.calls] == [DecisionMode.SHADOW]
    assert [mode for _, mode in right.calls] == [DecisionMode.SHADOW]


def test_a_reference_that_is_not_an_option_fails_closed(bundle: ProfileBundle) -> None:
    """打错一个字母的标签必须**在跑之前**红。

    它不会以「标签错了」的样子出现 —— 它会永远算作不一致，而报告上看起来像
    「这个后端在这一条上答错了」。一次由笔误造成的、指向后端的错误归因。
    """
    samples = (_sample("s1", "page_triage", reference="hihg"),)
    with pytest.raises(ConfigError, match="不是"):
        run_shadow(_frozen(bundle, samples), [_Stub("only")], bundle)


def test_a_reference_that_is_an_option_is_accepted(bundle: ProfileBundle) -> None:
    """正对照：一个**对**的标签不许被上一条所拒。"""
    samples = (_sample("s1", "page_triage", reference="high"),)
    report = run_shadow(_frozen(bundle, samples), [_Stub("only")], bundle)
    assert report.by_arm["only"].agrees_with_reference == 1


# --- 没作答的臂留在分组里 ---------------------------------------------------


def _verdict(bundle: ProfileBundle, plans: list[Any], *, reference: str | None = None) -> SampleVerdict:
    arms = [_Stub(f"arm{index}", plan=plan) for index, plan in enumerate(plans)]
    report = run_shadow(
        _frozen(bundle, (_sample("s1", "page_triage", reference=reference),)),
        arms,
        bundle,
    )
    return report.comparisons[0].verdict


def test_an_arm_that_could_not_answer_stays_in_the_grouping(bundle: ProfileBundle) -> None:
    """两条答了同一个词、一条弃权 —— 这是 ``PARTIAL_COVERAGE``，**不是**一致。

    把它记成一致，报告上就是一次 2-of-3 的「共识」，而那次共识里有一条从未发生的
    判定。这一条是本文件最要紧的判据。
    """
    verdict = _verdict(
        bundle,
        [_says("high"), _says("high"), _cannot(DecisionOutcome.UNAVAILABLE)],
    )
    assert verdict is SampleVerdict.PARTIAL_COVERAGE


def test_three_arms_that_all_abstained_are_not_agreement(bundle: ProfileBundle) -> None:
    """三个臂全都答不上来 = 共同阻塞，一个结论都没发生。"""
    verdict = _verdict(
        bundle,
        [
            _cannot(DecisionOutcome.ABSTAINED),
            _cannot(DecisionOutcome.ABSTAINED),
            _cannot(DecisionOutcome.UNAVAILABLE),
        ],
    )
    assert verdict is SampleVerdict.NO_COVERAGE


def test_two_different_answers_are_a_divergence(bundle: ProfileBundle) -> None:
    """两条答案不同，且都答了 —— 这才是本该比较的那件事。"""
    assert _verdict(bundle, [_says("high"), _says("low")]) is SampleVerdict.DIVERGED


def test_the_same_answer_from_every_arm_is_agreement(bundle: ProfileBundle) -> None:
    """正对照：全都答同一个词才是一致。"""
    assert _verdict(bundle, [_says("medium"), _says("medium")]) is SampleVerdict.AGREED


def test_a_single_arm_that_answered_is_agreement(bundle: ProfileBundle) -> None:
    """单臂：答了就是一致，没答就是没覆盖 —— 退化但不含糊。"""
    assert _verdict(bundle, [_says("high")]) is SampleVerdict.AGREED
    assert _verdict(bundle, [_cannot(DecisionOutcome.ABSTAINED)]) is SampleVerdict.NO_COVERAGE


def test_the_four_verdicts_are_exhaustive_and_exclusive(bundle: ProfileBundle) -> None:
    """四种结局覆盖了全部组合，且每一种都真的出现过。

    这不是枚举的自我检查：四种里的每一种都由上面一条真实用例命中过，而这一条把
    「判定函数的值域**恰好**是这四种」写下来 —— 少一种，某个组合会落进
    ``ValueError``；多一种，说明有组合没被想清楚。
    """
    assert {item.value for item in SampleVerdict} == {
        "agreed",
        "diverged",
        "partial_coverage",
        "no_coverage",
    }


def test_the_groups_still_name_every_arm(bundle: ProfileBundle) -> None:
    """分组里**每一个臂的名字都在**，包括没作答的那一组。"""
    arms = [_Stub("a", plan=_says("high")), _Stub("b", plan=_cannot(DecisionOutcome.ABSTAINED))]
    report = run_shadow(_frozen(bundle, (_sample("s1", "page_triage"),)), arms, bundle)
    groups = report.comparisons[0].groups
    assert sorted(name for group in groups for name in group) == ["a", "b"]
    assert len(groups) == 2


# --- 「没有答案」在报告里的形状 ---------------------------------------------


def test_a_yes_no_answer_has_no_comparable_form(bundle: ProfileBundle) -> None:
    """``YES_NO`` 的答案**没有**可比的形式，且这件事是显式拒绝的。

    影子比的是选项 key。把概率折成一个标签需要一个**阈值** —— 那正是本端口刻意
    不提供的东西（见 ``QuestionKind``）。今天三个 task 全是选择题，所以这条分支
    走不到；走不到不等于可以删，这里给它一条执行器。

    这条分支是**活着**的，不是防御性代码：走的是 :meth:`SampleComparison.answers`
    这个公开路径。
    """
    spec = DecisionSpec(
        spec_id="t:yesno",
        version="v" * 8,
        task=DecisionTask.PAGE_TRIAGE,
        questions=(
            DecisionQuestion(
                question_id="q", kind=QuestionKind.YES_NO, instructions="Is it?"
            ),
        ),
    )
    record = _record(
        "arm",
        DecisionCase(case_id="s1", spec=spec, state="一段文本"),
        answers=(YesNoAnswer(question_id="q", probability=0.7, confidence=None),),
    )
    comparison = SampleComparison(
        sample=_sample("s1", "page_triage"), spec_version=spec.version, records=(record,)
    )
    with pytest.raises(ConfigError, match="阈值"):
        comparison.answers


# --- 参考标签的分母 ---------------------------------------------------------


def test_an_unlabeled_sample_is_named_and_stays_out_of_the_denominator(
    bundle: ProfileBundle,
) -> None:
    """``reference is None`` 的样本**单独列出**，且不进分子也不进分母。

    把一个判不了的样本硬塞进某个选项，会让报告上的一次「答对」其实是一次巧合。

    **两半都要断言。** 只断言答对数不动是不够的：少了那个排除，没标签的样本会去和
    ``None`` 比，比不中，于是被记成一次**答错** —— 报告上出现一次对着不存在的标签
    的分歧，而「答对了 1 条」这个数字仍然是对的。变异测试就是这么发现这条漏掉的。
    """
    samples = (
        _sample("labelled", "page_triage", reference="high"),
        _sample("unlabelled", "page_triage", reference=None),
    )
    report = run_shadow(_frozen(bundle, samples), [_Stub("only", plan=_says("high"))], bundle)
    summary = report.by_arm["only"]
    assert summary.answered == 2
    assert summary.agrees_with_reference == 1, "只有带标签的那一条进分母"
    assert summary.disagrees_with_reference == 0, "没标签的那一条**也不进**答错的分母"
    assert report.unlabeled == ("unlabelled",)


def test_a_wrong_answer_is_counted_as_a_disagreement(bundle: ProfileBundle) -> None:
    """正对照：答错要进 ``disagrees_with_reference``，不能只数答对的。"""
    samples = (_sample("s1", "page_triage", reference="high"),)
    report = run_shadow(_frozen(bundle, samples), [_Stub("only", plan=_says("low"))], bundle)
    assert report.by_arm["only"].disagrees_with_reference == 1
    assert report.by_arm["only"].agrees_with_reference == 0


def test_an_arm_that_did_not_answer_is_not_scored(bundle: ProfileBundle) -> None:
    """没作答的臂**不**进与标签比对的分母 —— 它既不是答对也不是答错。

    它仍然进 ``answered / abstained / unavailable``，所以「它没答」这件事在报告里
    看得见，只是不以「答错」的样子出现。
    """
    samples = (_sample("s1", "page_triage", reference="high"),)
    report = run_shadow(
        _frozen(bundle, samples), [_Stub("only", plan=_cannot(DecisionOutcome.ABSTAINED))], bundle
    )
    summary = report.by_arm["only"]
    assert (summary.abstained, summary.agrees_with_reference, summary.disagrees_with_reference) == (1, 0, 0)


def test_the_arm_summary_has_no_rate_field() -> None:
    """``ArmSummary`` 的字段是**计数**，一个比率字段都没有。

    一个 ``accuracy: float`` 会逼人选一个分母，而三个分母（答了的 / 有标签的 /
    题面没漂移的）各不相同 —— 除法谁都可以做，而「按哪个分母」这件事在报告里
    看得见才作数。
    """
    assert {field.name for field in dataclasses.fields(ArmSummary)} == {
        "provider",
        "answered",
        "abstained",
        "unavailable",
        "agrees_with_reference",
        "disagrees_with_reference",
    }


# --- 题面漂移 ---------------------------------------------------------------


def test_a_reworded_option_stops_the_label_comparison(bundle: ProfileBundle) -> None:
    """题面改过一个字 ⇒ 那一族的旧标签**不再参与**比对，且这件事单列。

    这是 ``version`` 被发明出来防的那件事：不排除，一次题面改动会以「后端一致率
    下降」的样子出现在报告上，而处置完全指向错误的方向。

    用**改过题面的 bundle** 来跑，所以冻结的版本与现算的版本必然不同 —— 这一条
    不回声。
    """
    task = _task("page_triage")
    samples = (_sample("s1", "page_triage", reference="high"),)
    frozen = _frozen(bundle, samples)  # 用**原**题面冻结
    changed = _reworded(bundle, task, "high")

    report = run_shadow(frozen, [_Stub("only", plan=_says("high"))], bundle=changed)

    assert task in report.spec_drift
    assert report.spec_drift[task][0] == frozen.spec_versions[task]
    assert report.spec_drift[task][1] == compile_spec(changed, task).version
    assert report.spec_versions[task] == report.spec_drift[task][1], "报告记的是现算的那一版"
    assert report.drifted == ("s1",)
    assert report.by_arm["only"].agrees_with_reference == 0, "漂移过的样本不进与标签比对的分母"
    assert report.by_arm["only"].disagrees_with_reference == 0, "也不许被记成答错 —— 那是同一件事的另一半"
    assert report.by_arm["only"].answered == 1, "但它仍然答过了，这件事看得见"


def test_an_unchanged_spec_reports_no_drift(bundle: ProfileBundle) -> None:
    """正对照：题面没动过就不许报漂移（否则每次都报，等于没报）。"""
    samples = (_sample("s1", "page_triage", reference="high"),)
    report = run_shadow(_frozen(bundle, samples), [_Stub("only", plan=_says("high"))], bundle)
    assert report.spec_drift == {}
    assert report.drifted == ()
    assert report.by_arm["only"].agrees_with_reference == 1


# --- 没有裁决 ---------------------------------------------------------------


def _all_keys(value: Any) -> set[str]:
    """递归收集一个 JSON 结构里**所有层**的键。"""
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key))
            keys |= _all_keys(item)
    elif isinstance(value, list):
        for item in value:
            keys |= _all_keys(item)
    return keys


def _verdict_words(keys: set[str]) -> list[str]:
    """键集里出现的裁决词。子串匹配：``agreement_rate`` 里的 ``agreement`` 也算。"""
    return sorted(word for word in _FORBIDDEN_REPORT_KEYS if any(word in key for key in keys))


def test_the_verdict_word_scanner_actually_fires() -> None:
    """正对照：扫描器在一个**真的**带裁决字段的结构上必须报警。

    没有这一条，下面那条判据在一个 ``as_dict()`` 返回 ``{}`` 的实现上也是绿的。
    """
    assert _verdict_words(_all_keys({"by_arm": {"a": {"majority": "high"}}})) == ["majority"]
    assert _verdict_words(_all_keys({"agreement_rate": 0.5})) == ["agreement_rate"], "子串也要抓到"


def test_the_report_carries_no_verdict_field(bundle: ProfileBundle) -> None:
    """报告里没有任何一处替人裁决：没有多数表决、没有胜出者、没有一致率。"""
    samples = (_sample("s1", "page_triage", reference="high"),)
    report = run_shadow(
        _frozen(bundle, samples), [_Stub("a", plan=_says("high")), _Stub("b", plan=_says("low"))], bundle
    )
    assert _verdict_words(_all_keys(report.as_dict())) == []


def test_the_report_shape_is_exactly_this(bundle: ProfileBundle) -> None:
    """顶层键集对着写死的清单断言。

    「没有裁决字段」那条判据只能证明**没有**什么；这一条证明**有**什么，两者一起
    才把 ``as_dict`` 的形状钉住 —— 少了这一条，把整个报告删空也能通过上面那条。
    """
    report = run_shadow(_frozen(bundle, (_sample("s1", "page_triage"),)), [_Stub("a")], bundle)
    assert set(report.as_dict()) == {
        "spec_versions",
        "spec_drift",
        "samples_total",
        "samples_unlabeled",
        "samples_drifted",
        "by_verdict",
        "by_arm",
        "comparisons",
    }


def test_the_report_json_serialises(bundle: ProfileBundle) -> None:
    """报告要能逐字落盘 —— 枚举不能以 ``SampleVerdict.AGREED`` 的形状漏进去。"""
    report = run_shadow(_frozen(bundle, (_sample("s1", "page_triage"),)), [_Stub("a")], bundle)
    text = json.dumps(report.as_dict(), ensure_ascii=False)
    assert "SampleVerdict" not in text
    assert json.loads(text)["by_verdict"]["agreed"] == ["s1"]


def test_every_verdict_is_listed_even_when_empty(bundle: ProfileBundle) -> None:
    """四种判定**全部**列出，空的也是空表而不是缺席。

    只列命中的那几种，「后端们全都答得一样」与「一条都没跑成」在报告上都读不出来。
    """
    report = run_shadow(_frozen(bundle, (_sample("s1", "page_triage"),)), [_Stub("a")], bundle)
    assert set(report.by_verdict) == set(SampleVerdict)
    assert report.by_verdict[SampleVerdict.DIVERGED] == ()


# --- 影子没有 MCP 工具入口 --------------------------------------------------


def _imports_shadow(path: Path) -> bool:
    """这个文件有没有**真的**import 影子模块。

    AST 而不是文本匹配：``tools.py`` 的能力表里有一句散文写着「shadow / assist /
    future_auto」—— 那是在描述 ``DecisionMode`` 这个闭集，不是入口点。按词匹配会
    把一句正确的说明判成违规，而判据的误报会让人去改那句说明。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("shadow"):
            return True
        if isinstance(node, ast.Import):
            if any(alias.name.endswith("shadow") for alias in node.names):
                return True
    return False


def test_the_shadow_import_scanner_actually_fires(tmp_path: Path) -> None:
    """正对照：扫描器在一个**真的**import 了影子的文件上必须报警。"""
    path = tmp_path / "tool.py"
    path.write_text("from ...application.shadow import run_shadow\n", encoding="utf-8")
    assert _imports_shadow(path)
    path.write_text("import json\n", encoding="utf-8")
    assert not _imports_shadow(path), "干净的文本不许被误报"


def _code_strings(path: Path) -> list[str]:
    """文件里**代码用到的**字符串字面量 —— 不含 docstring。

    docstring 与注释是散文：它们能**描述**接线，但产生不了接线。而
    ``getattr(module, "run_shadow")`` 里那个字符串是**数据**，在语法树上是一个非
    docstring 的 ``Constant`` —— 两者分得开，所以判据只抓后者。

    这不是放松，是把误报关掉（同 ``_imports_shadow`` 那次）：上一版按**整段文本**
    匹配，于是任何一句「影子的调用点是 ``application/shadow.py::run_shadow``」都会
    被判成接线 —— 而那句说明是**对的**，判据红了之后唯一正确的动作是改判据。

    ``f"…"`` 不用单独处理：``ast.walk`` 会走进 ``JoinedStr.values``，其中的字面
    部分本身就是一个 ``Constant``。
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
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_the_dynamic_name_scanner_actually_fires(tmp_path: Path) -> None:
    """正对照：**代码里**的名字要报警，**散文里**的不许 —— 两半都要断言。

    只断言前者，一个「见到这个词就报警」的实现也能过；只断言后者，一个「什么都
    不报警」的实现也能过。而这条判据的全部内容恰好就是这个区分本身。
    """
    path = tmp_path / "tool.py"

    path.write_text('"""说明：这条接线会调到 run_shadow。"""\n', encoding="utf-8")
    assert not _hits(path, "run_shadow"), "docstring 是散文，不是接线"

    path.write_text('"""模块说明。"""\n\n\ndef f(module):\n    return getattr(module, "run_shadow")\n', encoding="utf-8")
    assert _hits(path, "run_shadow"), "getattr 里的那个名字就是接线"

    path.write_text('X = "run_shadow"\n', encoding="utf-8")
    assert _hits(path, "run_shadow"), "字符串常量也是数据"

    path.write_text('X = f"run_shadow"\n', encoding="utf-8")
    assert _hits(path, "run_shadow"), "f-string 的字面部分同样是数据"


def _hits(path: Path, needle: str) -> bool:
    return any(needle in text for text in _code_strings(path))


def test_no_adapter_reaches_the_shadow(repo_root: Path) -> None:
    """``marketops/adapters/`` 里没有一处 import 影子，也没有一处按名字调它。

    理由不是洁癖：§4 说影子模式下两个后端的答案**不交给研究侧**。一个可从 MCP 调到
    的影子，就是一个可以让 Agent 主动去问「模型会怎么答」的入口 —— 而那正是影子
    要避免的事。**不提供这个入口**优于提供之后附一条注意事项。

    两个扫描器各管一半：AST import 扫描看不见 ``getattr`` 那种写法，而按名字扫
    **代码里的字符串**看不见 ``from … import``。两半都要。

    扫全部 adapter 而不只 ``mcp_server/``：接线是**任何** adapter 接都算接线，而
    今天真正会被拿去接的是 MCP 那一侧。
    """
    adapters = repo_root / "marketops" / "adapters"
    files = sorted(adapters.rglob("*.py"))
    assert files, "扫描目录不存在或是空的 —— 这条判据会假绿"

    assert [path.name for path in files if _imports_shadow(path)] == []
    assert [path.name for path in files if _hits(path, "run_shadow")] == []
    # 正对照：同一个 AST 扫描器必须看得见这个目录里**确实在**的 import
    assert any(_imports_name(path, "decisions") for path in files)


def _imports_name(path: Path, needle: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        needle in (node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )


def test_the_shadow_imports_no_concrete_backend(repo_root: Path) -> None:
    """``application/shadow.py`` 不 import 任何 adapter。

    后端由调用方注入（``run_shadow`` 的参数）。影子里写死一个后端，就等于「比较
    这两个后端」这件事在模块里被决定了 —— 而它是一次实验的**参数**。
    """
    tree = ast.parse(
        (repo_root / "marketops" / "application" / "shadow.py").read_text(encoding="utf-8")
    )
    modules = [
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    ] + [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert not [name for name in modules if "adapters" in name]
    # 正对照：同一个解析器必须看得见这个文件**确实**import 的东西
    assert any("ports.decisions" in name for name in modules)


# --- 构造期的不变量 ---------------------------------------------------------


def test_a_sample_without_a_state_fails_closed() -> None:
    with pytest.raises(ConfigError, match="state"):
        ShadowSample(sample_id="s1", task=DecisionTask.PAGE_TRIAGE, state="   ")


def test_an_empty_reference_string_is_not_the_same_as_no_label() -> None:
    """空串与 ``None`` 在 JSON 里不同，在报告里也不许混同。"""
    with pytest.raises(ConfigError, match="没有标签"):
        ShadowSample(
            sample_id="s1", task=DecisionTask.PAGE_TRIAGE, state="文本", reference=""
        )


def test_an_empty_sample_set_fails_closed() -> None:
    """零条样本的报告与「没有分歧」长得一模一样。"""
    with pytest.raises(ConfigError, match="空"):
        FrozenSampleSet(samples=(), spec_versions={})


def test_a_duplicate_sample_id_fails_closed() -> None:
    samples = (_sample("s1", "page_triage"), _sample("s1", "page_triage"))
    with pytest.raises(ConfigError, match="两次"):
        FrozenSampleSet(
            samples=samples, spec_versions={DecisionTask.PAGE_TRIAGE: "v"}
        )


def test_a_sample_task_without_a_declared_version_fails_closed() -> None:
    """有样本却没声明那一族的题面版本 ⇒ 可比性无从判断。

    而判不出来的默认结果是**不比** —— 那会让整批样本静默地不进分母。
    """
    with pytest.raises(ConfigError, match="题面版本"):
        FrozenSampleSet(
            samples=(_sample("s1", "page_triage"),),
            spec_versions={DecisionTask.ROLE_FUNCTION: "v"},
        )


def test_run_shadow_needs_at_least_one_arm(bundle: ProfileBundle) -> None:
    with pytest.raises(ConfigError, match="至少"):
        run_shadow(_frozen(bundle, (_sample("s1", "page_triage"),)), [], bundle)


def test_two_arms_with_the_same_name_fail_closed(bundle: ProfileBundle) -> None:
    """回执按 ``name`` 认后端 —— 重名会把两臂的结果在报告里合成一格，条数仍正常。"""
    with pytest.raises(ConfigError, match="同一个 name"):
        run_shadow(
            _frozen(bundle, (_sample("s1", "page_triage"),)),
            [_Stub("same"), _Stub("same")],
            bundle,
        )


def test_a_task_the_profile_did_not_declare_fails_closed(bundle: ProfileBundle) -> None:
    """样本点名了一个 profile 没声明的 task ⇒ 编译期红，不是一列空白。

    这里用**造一个不问这个 task 的 profile** 的改法，而不是 monkeypatch。
    """
    declared = bundle.decisions
    assert declared is not None
    tasks = {task: spec for task, spec in declared.tasks.items() if task is not DecisionTask.PAGE_TRIAGE}
    stripped = dataclasses.replace(bundle, decisions=DeclaredDecisions(tasks=tasks))

    samples = (_sample("s1", "page_triage"),)
    frozen = FrozenSampleSet(samples=samples, spec_versions={DecisionTask.PAGE_TRIAGE: "v"})
    with pytest.raises(ConfigError, match="没有声明"):
        run_shadow(frozen, [_Stub("only")], stripped)


# --- 装载 -------------------------------------------------------------------


def _document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": SHADOW_SAMPLES_SCHEMA,
        "spec_versions": {"page_triage": "a" * 32, "role_function": "b" * 32},
        "samples": [
            {
                "sample_id": "s1",
                "task": "page_triage",
                "state": "一段冻结的页面描述",
                "reference": "high",
                "evidence_refs": ["sha256:abcdef"],
            },
            {"sample_id": "s2", "task": "role_function", "state": "一个岗位名"},
        ],
    }
    document.update(overrides)
    return document


def _written(tmp_path: Path, document: Any) -> Path:
    path = tmp_path / "samples.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def test_a_valid_sample_file_round_trips(tmp_path: Path) -> None:
    """正对照：一份**合法**的文件必须读得回来，且逐字对得上。

    没有这一条，下面所有「红」的判据在一个「任何文件都抛」的 loader 上也是绿的。
    """
    loaded = load_samples(_written(tmp_path, _document()))

    assert loaded.spec_versions == {
        DecisionTask.PAGE_TRIAGE: "a" * 32,
        DecisionTask.ROLE_FUNCTION: "b" * 32,
    }
    assert [sample.sample_id for sample in loaded.samples] == ["s1", "s2"]
    first = loaded.samples[0]
    assert (first.task, first.reference, first.evidence_refs) == (
        DecisionTask.PAGE_TRIAGE,
        "high",
        ("sha256:abcdef",),
    )
    assert loaded.samples[1].reference is None, "没写 reference 的样本是**没标签**，不是空标签"


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("schema", "schema"),
        ("no_versions", "spec_versions"),
        ("empty_versions", "spec_versions"),
        ("samples_not_a_list", "数组"),
        ("unknown_task", "非法 task"),
        ("sample_without_state", "state"),
        ("reference_not_a_string", "reference"),
        ("evidence_refs_not_a_list", "evidence_refs"),
        ("duplicate_sample_id", "两次"),
        ("row_not_an_object", "非对象"),
    ],
)
def test_the_loader_fails_closed(tmp_path: Path, mutation: str, expected: str) -> None:
    """每一条对应一类「文件被写坏了」，而它们的安静读法各自会造成一次错误归因。"""
    document = _document()
    if mutation == "schema":
        document["schema"] = "marketops/shadow-samples.v2"
    elif mutation == "no_versions":
        del document["spec_versions"]
    elif mutation == "empty_versions":
        document["spec_versions"] = {}
    elif mutation == "samples_not_a_list":
        document["samples"] = {"s1": {}}
    elif mutation == "unknown_task":
        document["samples"][1]["task"] = "page_triage_v2"
    elif mutation == "sample_without_state":
        document["samples"][1]["state"] = ""
    elif mutation == "reference_not_a_string":
        document["samples"][0]["reference"] = 1
    elif mutation == "evidence_refs_not_a_list":
        document["samples"][0]["evidence_refs"] = "sha256:abcdef"
    elif mutation == "duplicate_sample_id":
        document["samples"][1]["sample_id"] = "s1"
    elif mutation == "row_not_an_object":
        document["samples"][1] = "s2"
    else:  # pragma: no cover - 参数表写错了
        raise AssertionError(mutation)

    with pytest.raises(ConfigError, match=expected):
        load_samples(_written(tmp_path, document))


def test_a_missing_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="不存在"):
        load_samples(tmp_path / "nope.json")


def test_broken_json_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "samples.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="JSON"):
        load_samples(path)


def test_a_top_level_array_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="顶层"):
        load_samples(_written(tmp_path, [{"sample_id": "s1"}]))


def test_a_loaded_file_can_actually_be_run(tmp_path: Path, bundle: ProfileBundle) -> None:
    """装载 —— 运行是一条链，不是两段各自绿就算完。

    单独测 loader 只能证明它读得出形状；单独测 ``run_shadow`` 只能证明它能跑一个
    手工搭的 ``FrozenSampleSet``。而「文件里那个 ``"task": "page_triage"`` 字符串
    到底能不能编译出题面」只有连起来跑一次才知道。
    """
    document = _document(
        spec_versions={
            "page_triage": compile_spec(bundle, DecisionTask.PAGE_TRIAGE).version
        },
        samples=[
            {
                "sample_id": "s1",
                "task": "page_triage",
                "state": "一段冻结的页面描述",
                "reference": "high",
                "evidence_refs": ["sha256:abcdef"],
            }
        ],
    )

    report = run_shadow(
        load_samples(_written(tmp_path, document)), [_Stub("only", plan=_says("high"))], bundle
    )

    assert report.spec_drift == {}, "文件里的版本与现算的一致 ⇒ 不许报漂移"
    assert report.by_arm["only"].agrees_with_reference == 1
    assert report.comparisons[0].sample.evidence_refs == ("sha256:abcdef",)

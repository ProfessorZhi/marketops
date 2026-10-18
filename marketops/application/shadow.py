"""影子：多个决策后端跑**同一批冻结样本**，报告只报分歧。

``docs/REQUIREMENTS_DISCOVERY.md`` §9 Experiment 3：为 page triage /
responsibility relation / role-business-function classification 三族冻结带标签的
样本，「Compare rules / Jev / Laya / LLM structured output」。

## 这个模块回答什么，不回答什么

它回答：**同一份题面下，这几个后端在哪些样本上给出了不同的东西。**

它**不**回答「谁对」。这一族里最容易犯的错是给报告加一个 ``majority`` 字段 ——
三个后端里两个说了同一个词，看起来像一个可以拿去用的结论。而本仓的立场是明确的：
§4 要求 automated acceptance 在「独立留出评估证明那个确切的判定」之前保持关闭，
而**多数表决就是一个没人证明过的判定**。三个后端里两个错得一样是常态（它们是同一
个产品族的本地版与托管版），多数表决会把它记成一次共识，然后那个词会以「证据」的
面目往下走。

所以报告是**分组**，不是裁决：每个样本按答案分组，组数大于一就是一次分歧。字段里
没有 ``winner`` / ``best`` / ``consensus`` / ``accuracy`` 这种东西。

## 「没有答案」是一个**参与分组**的答案，而且不止一种

弃权与不可用**留在分组里**，不和「没作答的臂」一起被丢掉。丢掉它们，一个 2-of-3 的
分组就成了全体一致 —— 而那次「一致」里有一条从未发生的判定。

一条样本的结局因此是**四值**的，不是一个「有没有分歧」的布尔：:class:`SampleVerdict`
的 ``AGREED`` / ``DIVERGED`` / ``PARTIAL_COVERAGE`` / ``NO_COVERAGE``。压成布尔会
同时丢掉两类待办 —— 「覆盖率不够」（去把后端接上）与「两个后端答案不同」（这才是本
该比较的那件事）在界面上长得像，处置却是反的。三个臂全都弃权尤其不能算一致：那是
共同阻塞，一个结论都没有发生。四种状态都有执行器。

## 参考标签是**计数**，不是判词

冻结样本带一个参考标签（§9 的 "labeled samples"）。报告按臂数出答对与答错的**条数**
—— 只有计数，没有比率。理由是分母：

* ``UNAVAILABLE`` 的记录**不在分母里** —— 它既不是答对也不是答错，而任何一个比率都
  逼着人替它选一个归属；
* 参考标签可以是 ``None``（标注的人也放不进任何一个选项），那些样本单独列出；
* 题面换过之后，旧标签与新题面**不可比** —— 那些样本也单独列出，不进分母。

三个分母各不相同，而一个 ``accuracy: float`` 会把它们全部藏起来。数出来的条数与
分母就摆在那里，除法谁都可以做，而「按哪个分母」这件事在报告里看得见。

## 影子没有 MCP 工具入口

``adapters/mcp_server/`` 一行都不引用本模块，这有一条判据守着。理由不是洁癖：§4 说
影子模式下两个后端的答案**不交给研究侧**。一个可从 MCP 调到的影子，就是一个可以让
研究侧的 Agent 主动去问「模型会怎么答」的入口，而那正是影子要避免的事。

## 模式不是参数

:func:`run_shadow` **没有** ``mode`` 参数 —— 它恒以 ``DecisionMode.SHADOW`` 调用后端。
``DecisionProvider.decide`` 要求逐次给出模式（见 ``ports/decisions.py``，那个必填是
对的），而在**这里**把模式做成旋钮，等于给「一次影子判定被记成辅助判定」留了一个
入口，而两者的可见性不同：辅助的答案进研究轨迹，影子的不进。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..domain.enums import DecisionMode, DecisionOutcome, DecisionTask
from ..domain.errors import ConfigError
from ..domain.profile import ProfileBundle
from ..ports.decisions import (
    ChoiceAnswer,
    DecisionCase,
    DecisionProvider,
    DecisionRecord,
    DecisionSpec,
)
from .decision_specs import compile_spec

__all__ = [
    "SHADOW_SAMPLES_SCHEMA",
    "ArmSummary",
    "FrozenSampleSet",
    "SampleComparison",
    "SampleVerdict",
    "ShadowReport",
    "ShadowSample",
    "load_samples",
    "run_shadow",
]

#: 样本文件的 schema 标识。与 profile 的三份一样：**对不上就是装载失败**。
#: 一份样本清单是冻结的 —— 它被读进一份 schema 不同的代码里时，安静的读法会让
#: 「这一族样本是那一版冻结的」变成一句无法追溯的话。
SHADOW_SAMPLES_SCHEMA = "marketops/shadow-samples.v1"


@dataclass(frozen=True)
class ShadowSample:
    """一条冻结的输入，加它被标注的答案。

    ``state`` 是**冻结**的文本 —— 它不再从任何地方重新抓一遍。冻结的意义就在这里：
    后端换版本、站点改版、题面调整，这一批输入仍然是同一批，否则两次运行的分歧里
    混着输入本身的变化。

    ``reference`` 是标注的答案，取自该题面的选项 key。它是 ``None`` 的时候表示
    **标注的人也放不进任何一个选项** —— 那不是缺失值：一条把「我判不了」硬塞进某个
    选项的标签，会让报告上的一次「答对」其实是一次巧合。
    """

    sample_id: str
    task: DecisionTask
    state: str
    reference: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.sample_id:
            raise ConfigError("样本的 sample_id 不能为空 —— 报告按它对齐")
        if not self.state.strip():
            raise ConfigError(
                f"样本 {self.sample_id!r} 的 state 是空的 —— 一次没有输入的判定"
                "只会得到后端凭空的回答"
            )
        if self.reference is not None and not self.reference.strip():
            raise ConfigError(
                f"样本 {self.sample_id!r} 的 reference 是空串。\n"
                "  「没有标签」写成 null，不要写成空串 —— 两者在 JSON 里不同，"
                "而在报告里会让这条样本落进分母。"
            )


@dataclass(frozen=True)
class FrozenSampleSet:
    """一批冻结样本，加上它们**冻结时所依据的题面版本**。

    ``spec_versions`` 是这一批标签的可比性声明：标注是在某一版题面下做的，而
    题面一改（``application/decision_specs.py`` 的 ``version`` 是内容寻址的），
    那些标签就不再描述同一件事。声明而不是逐样本各写一遍：一次冻结只对应一份题面。

    这条不是洁癖。改了题面里的一个词，全部标签会**安静地**变成不可比，而重跑影子
    时那批样本上的不一致会以「后端退步了」的样子出现 —— 处置完全指向错误的方向。
    """

    samples: tuple[ShadowSample, ...]
    spec_versions: dict[DecisionTask, str]

    def __post_init__(self) -> None:
        if not self.samples:
            raise ConfigError(
                "冻结样本集是空的 —— 零条样本的影子报告与「没有分歧」长得一模一样"
            )
        seen: set[str] = set()
        for sample in self.samples:
            if sample.sample_id in seen:
                raise ConfigError(
                    f"样本 id {sample.sample_id!r} 出现了两次 —— 报告按它对样本，"
                    "重复的 id 会让两条样本的结果互相覆盖，而条数看起来是对的"
                )
            seen.add(sample.sample_id)
        missing = {sample.task for sample in self.samples} - set(self.spec_versions)
        if missing:
            raise ConfigError(
                f"这些 task 有样本却没有声明题面版本：{sorted(item.value for item in missing)}。\n"
                "  没有那一版，标签的可比性就无从判断，而判不出来的默认结果是不比 —— "
                "那会让整批样本静默地不进分母。"
            )


class SampleVerdict(str, Enum):
    """一条样本上，各臂**共同**呈现出什么。

    这是一个四分的**闭集**，不是一个「有没有分歧」的布尔 —— 因为那四种状态的处置
    是反的，而它们在界面上长得像：

    ``AGREED``           所有臂都答了，且答的是同一个词。一次发现。
    ``DIVERGED``         所有臂都答了，但不是同一个词。**这才是本该比较的那件事。**
    ``PARTIAL_COVERAGE`` 一部分臂答了，另一部分没答。要办的是**覆盖率**
                         （某个后端在这类输入上跑不起来），不是「谁对」。
    ``NO_COVERAGE``      一个臂都没答。共同阻塞：去把后端接上。

    把后两者压进「没有分歧」，报告会同时丢掉两类待办；把它们压进「有分歧」，读的
    人会去查模型，而该查的是部署。**全员弃权尤其不能算一致** —— 那是全都没发生。
    """

    AGREED = "agreed"
    DIVERGED = "diverged"
    PARTIAL_COVERAGE = "partial_coverage"
    NO_COVERAGE = "no_coverage"


@dataclass(frozen=True)
class SampleComparison:
    """一条样本上，各臂的答案。"""

    sample: ShadowSample
    #: 这次判定实际用的题面版本（从 profile 现算，不是样本里写的）。
    spec_version: str
    records: tuple[DecisionRecord, ...]

    @property
    def answers(self) -> dict[str, str | None]:
        """provider → 它的答案；``None`` = 这一臂没给出答案。"""
        return {record.provider: _answer_key(record) for record in self.records}

    @property
    def groups(self) -> tuple[tuple[str, ...], ...]:
        """按答案分组的 provider 名。

        **没作答的臂留在里面**，自成一组。把它们滤掉，一个 2-of-3 的分组就会显示成
        全体一致 —— 而那次「一致」里有一条从未发生的判定。
        """
        buckets: dict[str | None, list[str]] = {}
        for provider, answer in self.answers.items():
            buckets.setdefault(answer, []).append(provider)
        return tuple(tuple(names) for names in buckets.values())

    @property
    def verdict(self) -> SampleVerdict:
        """见 :class:`SampleVerdict`。四种状态互斥且穷尽。"""
        answers = self.answers
        gave = [answer for answer in answers.values() if answer is not None]
        if not gave:
            return SampleVerdict.NO_COVERAGE
        if len(gave) != len(answers):
            return SampleVerdict.PARTIAL_COVERAGE
        return SampleVerdict.AGREED if len(set(gave)) == 1 else SampleVerdict.DIVERGED


@dataclass(frozen=True)
class ArmSummary:
    """一个后端的条数。**只有计数，没有比率** —— 见模块 docstring 的分母那段。"""

    provider: str
    answered: int
    abstained: int
    unavailable: int
    #: 与参考标签一致 / 不一致的**条数**。分母是
    #: ``answered`` 减去「没标签的」再减去「题面漂移的」—— 那两者在报告里各自列出。
    agrees_with_reference: int
    disagrees_with_reference: int


@dataclass(frozen=True)
class ShadowReport:
    """一次影子运行的结果。**没有裁决字段。**"""

    #: 这次实际用到的题面版本，按 task。跨运行对比读它。
    spec_versions: dict[DecisionTask, str]
    comparisons: tuple[SampleComparison, ...]
    by_arm: dict[str, ArmSummary]
    #: 冻结版本与现算版本不一致的 task → （冻结的, 现算的）。这些 task 上的样本
    #: **不参与**与标签的比对。
    spec_drift: dict[DecisionTask, tuple[str, str]]

    @property
    def by_verdict(self) -> dict[SampleVerdict, tuple[str, ...]]:
        """四种判定各自命中了哪些样本 id。

        **四种全部列出**，包括 ``AGREED`` —— 只列分歧的话，「这一族样本后端们全都
        答得一样」与「这一族样本一条都没跑成」在报告上都是空表。
        """
        buckets: dict[SampleVerdict, list[str]] = {verdict: [] for verdict in SampleVerdict}
        for item in self.comparisons:
            buckets[item.verdict].append(item.sample.sample_id)
        return {verdict: tuple(ids) for verdict, ids in buckets.items()}

    @property
    def unlabeled(self) -> tuple[str, ...]:
        """没有参考标签的样本 id —— 它们**不在**与标签比对的分母里。"""
        return tuple(item.sample.sample_id for item in self.comparisons if item.sample.reference is None)

    @property
    def drifted(self) -> tuple[str, ...]:
        """题面换过、标签不可比的样本 id。"""
        return tuple(
            item.sample.sample_id
            for item in self.comparisons
            if item.sample.task in self.spec_drift
        )

    def as_dict(self) -> dict[str, Any]:
        """给报告写盘用的形状。**逐条列出样本级的分组**，不只给总数。

        只给总数的话，「三条分歧」在读的人那里没有可处置的下一步 —— 他不知道是哪
        三条、也不知道分歧的形状（2-against-1 与三条各不相同是两件事）。

        **没有裁决字段**：这里没有 ``majority`` / ``winner`` / ``consensus`` /
        ``accuracy``，也不会有。见模块 docstring。这一条有执行器
        （``tests/test_shadow.py`` 对着写死的键集断言）。
        """
        return {
            "spec_versions": {
                task.value: version for task, version in self.spec_versions.items()
            },
            "spec_drift": {
                task.value: {"frozen": pair[0], "compiled": pair[1]}
                for task, pair in self.spec_drift.items()
            },
            "samples_total": len(self.comparisons),
            "samples_unlabeled": list(self.unlabeled),
            "samples_drifted": list(self.drifted),
            "by_verdict": {
                verdict.value: list(ids) for verdict, ids in self.by_verdict.items()
            },
            "by_arm": {
                name: {
                    "answered": summary.answered,
                    "abstained": summary.abstained,
                    "unavailable": summary.unavailable,
                    "agrees_with_reference": summary.agrees_with_reference,
                    "disagrees_with_reference": summary.disagrees_with_reference,
                }
                for name, summary in self.by_arm.items()
            },
            "comparisons": [
                {
                    "sample_id": item.sample.sample_id,
                    "task": item.sample.task.value,
                    "reference": item.sample.reference,
                    "spec_version": item.spec_version,
                    "answers": item.answers,
                    "groups": [list(group) for group in item.groups],
                    "verdict": item.verdict.value,
                }
                for item in self.comparisons
            ],
        }


def run_shadow(
    sample_set: FrozenSampleSet,
    providers: Iterable[DecisionProvider],
    bundle: ProfileBundle,
) -> ShadowReport:
    """让每个后端对每条样本各判一次，返回分组结果。

    **没有 ``mode`` 参数**：恒以 ``DecisionMode.SHADOW`` 调用。见模块 docstring。

    不跳过 ``ready`` 为假的后端 —— 它给出的 ``UNAVAILABLE`` 记录是一条**结果**，
    报告要看得见它。跳过的话，「jev 缺凭据」在报告上会变成「jev 没参试」，而那是
    一件完全不同的事。§20：缺凭据不得写 PASS，也不得写成一格空白。
    """
    arms = list(providers)
    if not arms:
        raise ConfigError("影子至少要一个后端 —— 零个后端的报告与「没有分歧」一样")
    names = [provider.name for provider in arms]
    if len(set(names)) != len(names):
        raise ConfigError(
            f"两个后端用了同一个 name：{names}。\n"
            "  回执按 name 认后端，重名会让两臂的结果在报告里合成一格，"
            "而条数看起来仍然正常。"
        )

    spec_versions: dict[DecisionTask, str] = {}
    comparisons: list[SampleComparison] = []
    for sample in sample_set.samples:
        spec = compile_spec(bundle, sample.task)
        spec_versions[sample.task] = spec.version
        if sample.reference is not None:
            _require_reference_is_a_choice(sample, spec)
        case = DecisionCase(
            case_id=sample.sample_id,
            spec=spec,
            state=sample.state,
            evidence_refs=sample.evidence_refs,
        )
        comparisons.append(
            SampleComparison(
                sample=sample,
                spec_version=spec.version,
                records=tuple(
                    provider.decide(case, mode=DecisionMode.SHADOW) for provider in arms
                ),
            )
        )

    spec_drift = {
        task: (frozen, spec_versions[task])
        for task, frozen in sample_set.spec_versions.items()
        if task in spec_versions and frozen != spec_versions[task]
    }
    return ShadowReport(
        spec_versions=spec_versions,
        comparisons=tuple(comparisons),
        by_arm=_summarize(comparisons, arms, spec_drift),
        spec_drift=spec_drift,
    )


def load_samples(path: Path) -> FrozenSampleSet:
    """读一份冻结的样本文件（``marketops/shadow-samples.v1``）。

    形状错在这里就红：一条 ``task`` 写错的样本，在报告上会以「这个后端没作答」
    （因为题面编译不出来）的形式出现，而不是以「有人把 task 名打错了」的形式。
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"冻结样本文件不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"冻结样本不是合法 JSON：{path}：{exc}") from exc
    if not isinstance(document, dict):
        raise ConfigError(f"冻结样本的顶层必须是对象：{path}")

    schema = document.get("schema")
    if schema != SHADOW_SAMPLES_SCHEMA:
        raise ConfigError(
            f"{path} 的 schema 是 {schema!r}，本 loader 只支持 {SHADOW_SAMPLES_SCHEMA!r}。\n"
            "  一份冻结清单是**冻结过的**：按一个不认识的版本读它，"
            "「这一族样本是那一版冻结的」这句话就再也追不回来了。"
        )

    raw_versions = document.get("spec_versions")
    if not isinstance(raw_versions, dict) or not raw_versions:
        raise ConfigError(
            f"{path} 缺少 spec_versions —— 标签是按某一版题面标的，"
            "没有那一版就无法判断它今天还可不可比"
        )
    versions = {
        _as_task(name, path): _as_text(value, f"spec_versions.{name}", path)
        for name, value in raw_versions.items()
    }

    raw_samples = document.get("samples")
    if not isinstance(raw_samples, list):
        raise ConfigError(f"{path} 的 samples 必须是数组")

    samples = tuple(_as_sample(item, path) for item in raw_samples)
    return FrozenSampleSet(samples=samples, spec_versions=versions)


# --- 内部 -------------------------------------------------------------------


def _answer_key(record: DecisionRecord) -> str | None:
    """一条回执的**可比答案**；没作答时是 ``None``。

    只有 ``CHOICE`` 的答案有可比的形式 —— 它的取值本来就来自一个闭集。``YES_NO``
    报的是概率，而**把概率折成一个标签需要一个阈值**，那正是本端口刻意不提供的
    东西（见 ``QuestionKind``）。所以这里显式拒绝，而不是发明一个。

    今天三个 task 全是选择题，所以这一条走不到 —— 走不到不等于可以删：一份加进
    ``YES_NO`` 的题面会在**下一次**运行里走到这里，而那时它需要的正是这段话说清
    该由谁来定义比较方式。
    """
    if record.outcome is not DecisionOutcome.ANSWERED:
        return None
    choices = []
    for answer in record.answers:
        if not isinstance(answer, ChoiceAnswer):
            raise ConfigError(
                f"记录 {record.record_id!r} 里有一条不是选择题的回答 —— 影子比的是"
                "选项 key，而概率没有可比的形式。把概率折成标签需要一个没人授权过的"
                "阈值；要比较这一族，先定义比较方式，别在这里发明一个。"
            )
        choices.append(answer.choice)
    if len(choices) != 1:
        raise ConfigError(
            f"记录 {record.record_id!r} 有 {len(choices)} 条回答，而影子按「一题一答案」"
            "分组。多题的题面需要逐题比较 —— 那是一种比较方式，得先定义出来。"
        )
    return choices[0]


def _summarize(
    comparisons: list[SampleComparison],
    arms: list[DecisionProvider],
    spec_drift: dict[DecisionTask, tuple[str, str]],
) -> dict[str, ArmSummary]:
    """按臂数条数。**三个分母各自成立，所以这里只出计数。**"""
    summaries: dict[str, ArmSummary] = {}
    for provider in arms:
        answered = abstained = unavailable = agree = disagree = 0
        for item in comparisons:
            record = next(
                (rec for rec in item.records if rec.provider == provider.name), None
            )
            if record is None:  # pragma: no cover - 上面每条样本都问过每一个臂
                continue
            if record.outcome is DecisionOutcome.ANSWERED:
                answered += 1
            elif record.outcome is DecisionOutcome.ABSTAINED:
                abstained += 1
            else:
                unavailable += 1

            # 分母是「有标签 **且** 题面没漂移 **且** 答了」。
            if item.sample.reference is None:
                continue
            if item.sample.task in spec_drift:
                continue
            if record.outcome is not DecisionOutcome.ANSWERED:
                continue
            if _answer_key(record) == item.sample.reference:
                agree += 1
            else:
                disagree += 1
        summaries[provider.name] = ArmSummary(
            provider=provider.name,
            answered=answered,
            abstained=abstained,
            unavailable=unavailable,
            agrees_with_reference=agree,
            disagrees_with_reference=disagree,
        )
    return summaries


def _require_reference_is_a_choice(sample: ShadowSample, spec: DecisionSpec) -> None:
    """标签必须是该题面的一个选项 key。

    打错一个字母的标签会**永远**算作不一致，而报告上看起来像「这个后端在这一条上
    答错了」—— 一次由笔误造成的、指向后端的错误归因。
    """
    allowed = spec.choices_by_question[spec.questions[0].question_id]
    if sample.reference not in allowed:
        raise ConfigError(
            f"样本 {sample.sample_id!r} 的参考标签 {sample.reference!r} 不是 "
            f"{spec.spec_id} 的一个选项。允许取值：{list(allowed)}"
        )


def _as_task(value: object, path: Path) -> DecisionTask:
    text = _as_text(value, "spec_versions 的键", path)
    try:
        return DecisionTask(text)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in DecisionTask)
        raise ConfigError(
            f"{path} 的 spec_versions 有非法 task：{text!r}。允许取值：{allowed}"
        ) from exc


def _as_text(value: object, field: str, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path} 的 {field} 必须是非空字符串，实际是 {value!r}")
    return value.strip()


def _as_sample(item: object, path: Path) -> ShadowSample:
    if not isinstance(item, dict):
        raise ConfigError(f"{path} 的 samples 里有非对象条目：{item!r}")
    task = _as_task(item.get("task"), path)
    reference = item.get("reference")
    if reference is not None and not isinstance(reference, str):
        raise ConfigError(
            f"{path} 的样本 {item.get('sample_id')!r} 的 reference 既不是字符串"
            f"也不是 null：{reference!r}"
        )
    refs = item.get("evidence_refs") or []
    if not isinstance(refs, list):
        raise ConfigError(f"{path} 的样本 {item.get('sample_id')!r} 的 evidence_refs 必须是数组")
    return ShadowSample(
        sample_id=_as_text(item.get("sample_id"), "samples[].sample_id", path),
        task=task,
        state=_as_text(item.get("state"), "samples[].state", path),
        reference=reference,
        evidence_refs=tuple(_as_text(ref, "samples[].evidence_refs[]", path) for ref in refs),
    )

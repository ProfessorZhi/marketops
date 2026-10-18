"""决策端口：一次**有界的语义判断**，以及它的两个后端（Jev / Laya）。

## 为什么这个文件今天才存在

B3 立的判据：**端口在第二个实现出现时才提取**（``docs/MVP_MIGRATION_PLAN.md``
§B3 偏离一）。本模块因此与 ``adapters/jev.py``、``adapters/laya.py`` **同一个
提交**落地 —— 在那之前写下的接口不是「先行设计」，而是一份看起来已经设计完了的
猜测：它会先被 review 通过、被引用、被写进文档，然后在第一个真实实现落地时按那个
实现的形状重写一遍。

两个实现的形状差得很远（一个是本地 421M 参数的 checkpoint，一个是托管 API），
所以这里的每一个字段都是**两边都能诚实填写**的那一种。凡是只有一边知道的东西，
它不在这里 —— 要么进 ``route`` 那个由后端自报的字符串，要么根本不记。

## 三处刻意**不提供**的入口

1. **没有「由调用方指定模型 / 阈值 / 题面」的参数。** §4：普通 worker 不得自造
   DecisionSpec，也不得扩大模型预算。题面（``DecisionSpec``）由服务端持有
   （``application/decision_specs.py`` 从冻结的 profile 编译出来），
   ``decide`` 只承载它 —— 工具面收到的是 **spec_id**，不是题面本身。
2. **没有打分题**（Likert / 程度分）。理由写在 ``QuestionKind`` 上：一个分数要变成
   结论必须再选一个阈值，而 §4 禁止没有独立评估支撑的阈值。
3. **没有 ``confidence`` 的缺省值，也没有从概率推出来的标签。** 后端报了什么就记
   什么，没报就是 ``None``。§4 逐字：**不得编造不受支持的概率值**。这一条在
   ``YesNoAnswer`` 上最尖锐 —— 后端给的是 ``p(yes)``，而把它按 0.5 变成
   ``"yes"``/``"no"`` 就是一次没人授权的阈值判定，所以本端口**不存标签**。

## 一条不能省的区别：ABSTAINED 与 UNAVAILABLE

见 ``DecisionOutcome``。它们在报告里长得像（都没有答案），处置却是反的：
前者要换后端或换输入，后者要把它接上。压成一个布尔之后，「这个 Scene 缺一个
决策后端」与「这份输入两个后端都不肯答」在运维界面上不可区分。

## 关于 ``record_id`` 为什么住在这里

它是**两个实现都必须算出同一个值**的推导（同一次判定在两条路径上必须得到同一个
id），所以它不能各写一遍。而它放在 ``ports/`` 而不是 ``domain/``：领域层看不见
``DecisionCase`` —— 那是本模块的类型，而依赖方向是 ports → domain，反过来不成立。
（同形的一处先例是 ``domain/budget.py::reservation_id``：那里两边都在领域层，
所以它住在领域层。）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..domain.enums import DecisionMode, DecisionOutcome, DecisionTask, QuestionKind
from ..domain.errors import ConfigError
from ..domain.profile import SceneFiles
from ..domain.util import sha256_text

#: 分段拼接的分隔符。与 ``domain/budget.py::SEP`` 同一个理由：三个分段都可能含
#: ``:``（``case_id`` 是 ``<profile>:<account>`` 这类形状），而 ``("a:b", "c")``
#: 与 ``("a", "b:c")`` 拼出来必须不同。US（U+001F）是控制字符，不可能出现在
#: 用户输入里，所以拼接天然无歧义。
_SEP = "\x1f"

#: ``record_id`` 取摘要的前多少位。与 ``domain/budget.py`` 的 32 位一致。
_ID_LENGTH = 32


@dataclass(frozen=True)
class DecisionOption:
    """一个选项：一个 key，加一句**给人读**的描述。

    ``key`` 是台账与比较报告里用的那个（机器读的），``description`` 是题面的一部分
    —— 它进模型。两者都要，因为把它们合成一个「人读的字符串」之后，同一条结论在
    两次运行之间可能因为描述改了一个字而变成不可比。
    """

    key: str
    description: str


@dataclass(frozen=True)
class DecisionQuestion:
    """一道题。**服务端持有** —— 调用方拿到的是它，但不能自己造一个。

    Args:
        question_id: 一次判定内唯一。回执按它对齐，比较报告也按它对齐。
        kind: 问法。见 ``QuestionKind`` —— 只有两种。
        instructions: 给模型的那一句（英文，因为两个后端的训练语料都是英文）。
        options: ``CHOICE`` 题的选项，**至少两个**；``YES_NO`` 题必须为空。
    """

    question_id: str
    kind: QuestionKind
    instructions: str
    options: tuple[DecisionOption, ...] = ()

    def __post_init__(self) -> None:
        """把一个**写坏了**的题面在**它被构造的那一刻**拒掉。

        这不是风格问题：题面是服务端从冻结的 profile 编译出来的，所以这里的每一条
        校验都对应一类「profile 被改坏了」—— 而它必须在装载期暴露，不是在某个账号
        的 run 跑到一半时以「模型答了一个不存在的选项」的形式暴露。后者会产出**一条
        看起来正常的记录**，而它的取值在闭集之外。

        ``YES_NO`` 不许带 options 同样是这一条：带了就说明有人想在上面加一层阈值
        （「偏 yes 还是偏 no」），而那正是本端口刻意不提供的东西。
        """
        if not self.question_id:
            raise ConfigError("题目的 question_id 不能为空 —— 回执按它对齐")
        if not self.instructions.strip():
            raise ConfigError(f"题目 {self.question_id!r} 没有 instructions")
        if self.kind is QuestionKind.YES_NO:
            if self.options:
                raise ConfigError(
                    f"题目 {self.question_id!r} 是 YES_NO，却带了 "
                    f"{len(self.options)} 个选项。是否题的答案由后端报出的概率承载，"
                    "本端口不提供「把它变成标签」的入口 —— 那需要一个没人授权过的阈值。"
                )
            return
        if len(self.options) < 2:
            raise ConfigError(
                f"题目 {self.question_id!r} 是 CHOICE，只有 {len(self.options)} 个选项。"
                "一个选项的选择题没有信息量，而它会安静地让每一次判定都答同一项。"
            )
        keys = [option.key for option in self.options]
        if len(set(keys)) != len(keys):
            raise ConfigError(f"题目 {self.question_id!r} 的选项 key 有重复：{keys}")
        for option in self.options:
            if not option.key or not option.description.strip():
                raise ConfigError(
                    f"题目 {self.question_id!r} 有一个选项缺 key 或描述：{option!r}"
                )


@dataclass(frozen=True)
class DecisionSpec:
    """一次判定的**完整题面**：判什么，以及问哪几道题。

    ``version`` 是必填的，而且它与 ``spec_id`` 一起进 ``input_sha256``：改了题面
    却沿用同一个 id 时，新旧两条记录会被读成同一件事的两次观测。带上版本之后，
    「这次不一致是不是因为我改了题面」在台账上是可以回答的。

    题面**不含**任何后端相关的参数（checkpoint 名、温度、模型 id）：那些是部署
    配置。Spec 里出现它们，一个 Scene 的判定就绑死在一个后端上了。
    """

    spec_id: str
    version: str
    task: DecisionTask
    questions: tuple[DecisionQuestion, ...]

    def __post_init__(self) -> None:
        if not self.spec_id or not self.version:
            raise ConfigError("DecisionSpec 的 spec_id 与 version 都是必填的")
        if not self.questions:
            raise ConfigError(f"spec {self.spec_id!r} 一道题都没有")
        ids = [question.question_id for question in self.questions]
        if len(set(ids)) != len(ids):
            raise ConfigError(f"spec {self.spec_id!r} 的 question_id 有重复：{ids}")

    @property
    def choices_by_question(self) -> dict[str, tuple[str, ...]]:
        """每题允许的取值，**只含选择题**。

        这是「回答里出现了一个闭集之外的选项」这类校验的唯一依据 —— 两个实现都要
        用它，所以它长在题面上，而不是各 adapter 里各写一遍。
        """
        return {
            question.question_id: tuple(option.key for option in question.options)
            for question in self.questions
            if question.kind is QuestionKind.CHOICE
        }

    def as_questions(self) -> dict[str, dict[str, object]]:
        """题面的**线上形状**：两个后端共同接受的那一种。

        这份映射**只有一处**，不是两个 adapter 各写一遍 —— 而这不是风格问题。

        laya 与 Jev 是同一个产品族的本地版与托管版，它们的题面 / 回执线上形状是
        **一样的**（``{"type", "instructions", "criteria"}``；laya 的
        ``_to_internal`` 读的就是这三个键）。两个 adapter 各自 lowering 一遍，则
        「某一题的 ``noul`` 被翻成了 ``choice``」这类漂移会让**同一个题面被两个后端
        问成了不同的问题** —— 而影子的存在就是为了比较它们的答案。那一次漂移会以
        「两个模型不一致」的样子出现在报告上，处置完全指向错误的方向（去查模型，
        而该查的是我们自己的翻译）。**翻译错误的形状恰好是一次发现。**

        两个后端对 ``criteria`` 的读法也一致：给一个字符串就是描述，laya 的
        ``render_options`` 渲染成 ``"<key>: <description>"``，Jev 的
        ``ChoiceQuestion.criteria`` 逐字收下。``YES_NO`` 因此**不带 criteria**：我们
        没有 true / false 的描述可给（``DecisionQuestion`` 要求 ``YES_NO`` 的 options
        为空），而一个「声明了却没人填」的字段比缺席更坏。两个后端都把缺席读成「没有
        描述」，并各自回落到它自己那句测过的默认措辞。
        """
        questions: dict[str, dict[str, object]] = {}
        for question in self.questions:
            payload: dict[str, object] = {
                "type": "choice" if question.kind is QuestionKind.CHOICE else "noul",
                "instructions": question.instructions,
            }
            if question.kind is QuestionKind.CHOICE:
                payload["criteria"] = {
                    option.key: option.description for option in question.options
                }
            questions[question.question_id] = payload
        return questions


@dataclass(frozen=True)
class DecisionCase:
    """一份**冻结**的输入：题面 + 题面要判的那段文本 + 它从哪来。

    ``state`` 是准备好的证据文本（有界、且**不得静默截断** —— 见
    ``DecisionPreprocessing`` 与两个 adapter 里的长度检查）。

    ``evidence_refs`` 是这段文本的可追溯性。它**允许为空**（一次探索性的判定可以
    指不向任何已存证据），但空与不空在报告上必须分得开 —— 一条无法回溯到证据的
    判定，它的可信度与一条能回溯的不同，而这件事只有记录里写下来才看得见。
    """

    case_id: str
    spec: DecisionSpec
    state: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.case_id:
            raise ConfigError("DecisionCase 的 case_id 是必填的")
        if not self.state.strip():
            raise ConfigError(
                f"case {self.case_id!r} 的 state 是空的 —— 一次没有输入的判定"
                "只会得到后端凭空的回答"
            )

    @property
    def input_sha256(self) -> str:
        """这次输入的指纹：题面版本 + 题目清单 + 文本 + 证据引用。

        **算出来的，不是存下来的。** §4 要求记录里带「结构化输入/证据哈希」，
        而一个可以传进来的字段意味着它可以与文本不一致 —— 那种不一致在事后完全
        看不出来（两条记录写着同一个哈希，判的却是两段不同的文本）。做成 property
        之后它不可能说谎。同形的一处先例：E3 把 ``extracts_links`` 变成
        ``ParsedDocument`` 的必填字段，理由也是「两处不可能各说各的」。
        """
        return sha256_text(
            _SEP.join(
                (
                    self.spec.spec_id,
                    self.spec.version,
                    ",".join(question.question_id for question in self.spec.questions),
                    self.state,
                    ",".join(self.evidence_refs),
                )
            )
        )


@dataclass(frozen=True)
class ChoiceAnswer:
    """选择题的回答。"""

    question_id: str
    choice: str
    #: 后端报出来的分布。``None`` 表示**它没报** —— 不是「均匀分布」，
    #: 也不是「我们只有 top-1」。两者的处置不同：前者是这条记录的已知边界，
    #: 后者是一次静默的信息损失。
    distribution: tuple[tuple[str, float], ...] | None
    #: 后端自己的置信。``None`` 同上。
    confidence: float | None


@dataclass(frozen=True)
class YesNoAnswer:
    """是否题的回答：**只有概率，没有标签**。

    后端（laya 的 ``noul``）报的是 ``p(yes)``。把它按 0.5 折成 ``"yes"``/``"no"``
    是一次阈值判定，而 §4 要求任何阈值都要有独立留出评估支撑 —— 本构建没有做过
    那次评估，所以这里不提供那个字段。想要一个二值结论的调用方必须自己写下一个
    阈值，而那件事会留在**它自己**的代码里，不会伪装成后端给的答案。
    """

    question_id: str
    probability: float
    confidence: float | None


#: 回执里的一条回答。两个成员各自携带**它的后端真的报了的东西** —— 没有哪个字段
#: 是「另一个成员才填」的。
DecisionAnswer = ChoiceAnswer | YesNoAnswer


@dataclass(frozen=True)
class DecisionPreprocessing:
    """推理**之前**对输入做了什么。§4 要求它进记录，理由是可比性。

    两个后端都会做「检测语言、选 checkpoint、量长度」这三件事，而具体做法不同。
    ``route`` 是后端**自己的话**（laya 的 ``RouteDecision.reason`` 是一句英文），
    因为它是最能解释「为什么这次选了这个 checkpoint」的那句话，而重新措辞会把
    它的取值绑在我们的措辞上。

    这里**没有** ``truncated`` 字段：本端口的纪律是输入不合窗口就弃权
    （``ABSTAINED`` + 理由），绝不裁剪。一个恒为 ``False`` 的字段比没有这个字段
    更坏 —— 它会让读的人以为「裁剪这件事被考虑过了，只是这次没发生」。
    """

    #: 检测到的语言。``None`` 表示后端没做检测（或检测不出来）—— 不是 "en"。
    language: str | None
    #: 选中的模型 / checkpoint，以及为什么。后端自报。
    route: str
    #: 送进推理的字符数。**总是有**：它是调用方这一侧就数得出来的量。
    input_chars: int


@dataclass(frozen=True)
class DecisionUsage:
    """后端报出来的用量。**未知就是未知。**

    ``billed`` 说的是「这笔调用有没有产生**外部计量成本**」：laya 是本地进程
    ⇒ ``False``；Jev 是外部 API ⇒ ``True``。这里**刻意没有金额** —— §8 要求
    「未知的外部用量在对账之前保持不确定」，而一个当场算出来的金额会在对账时
    被发现对不上，且那时已经有一份写着数字的报告在流通。真实成本记在预算账本
    （``BudgetLine.INFERENCE``）上，由两段式预留与结算管。

    ``input_tokens`` / ``output_tokens`` 为 ``None`` 表示后端没报 —— 不是 0。
    """

    input_tokens: int | None
    output_tokens: int | None
    billed: bool


@dataclass(frozen=True)
class DecisionRecord:
    """一次判定的完整回执。字段逐条对应 ``docs/MCP_SERVICE.md`` §4 那一句列举。

    「decision/spec version、结构化输入/证据哈希、provider/model/checkpoint 与
    preprocessing、答案、后端报出的概率（若有）、延迟、已知用量/成本、失败或弃权
    理由」—— 一条不多，一条不少。

    ``outcome`` 不是 ``ANSWERED`` 时 ``reason`` **必非空**（由 ``__post_init__``
    强制）。一个没有理由的弃权在报告里与一次崩溃无法区分。

    ## ``model`` 与 ``checkpoint`` 是两个不同的东西

    * ``checkpoint`` —— **我们部署的那一个**：本地权重的目录（laya），或请求里
      钉住的模型名（Jev）。它是我们的选择，不随后端变化。
    * ``model`` —— **回执自称的那一个**：答这一段话的东西。

    两个都记而不是合一：一个托管的模型名会**在服务端翻页**（``jev-latest`` 指向
    的权重换了），而本地目录的路径不变而内容被换掉是同一类事。那时 ``model`` 与
    ``checkpoint`` 出现分歧 —— 而分歧本身就是要看见的那件事，合成的字段会让它
    消失。
    """

    record_id: str
    case_id: str
    spec_id: str
    spec_version: str
    input_sha256: str
    provider: str
    model: str
    checkpoint: str
    preprocessing: DecisionPreprocessing
    outcome: DecisionOutcome
    answers: tuple[DecisionAnswer, ...]
    reason: str
    latency_ms: int
    usage: DecisionUsage
    decided_at: str
    mode: DecisionMode
    #: 这段输入是从哪几条证据来的。**逐字带过来**，与 ``case.evidence_refs`` 同源。
    evidence_refs: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.outcome is not DecisionOutcome.ANSWERED and not self.reason.strip():
            raise ConfigError(
                f"记录 {self.record_id!r} 的 outcome 是 {self.outcome.value}，"
                "却没有理由 —— 没有理由的弃权与一次崩溃在报告上无法区分"
            )
        answered = {answer.question_id for answer in self.answers}
        if self.outcome is DecisionOutcome.ANSWERED and not answered:
            raise ConfigError(
                f"记录 {self.record_id!r} 声明 ANSWERED，却一条回答都没有"
            )

    @property
    def answered(self) -> bool:
        """这一条能不能当结论用。三个 outcome 里只有一个是 ``True``。"""
        return self.outcome is DecisionOutcome.ANSWERED


def answers_from_payload(
    payload: Mapping[str, Mapping[str, object]],
    spec: DecisionSpec,
    *,
    provider: str,
) -> tuple[DecisionAnswer, ...]:
    """把后端报出的**线上回执**翻成端口自己的回答，逐题校验。

    与 :meth:`DecisionSpec.as_questions` 是同一件事的另一半，理由也一样：两个后端
    的回执形状相同（``{"type": "choice", "choice", "probabilities", "confidence"}``
    与 ``{"type": "noul", "noul"}``），两处各翻一遍就会漂移，而漂移的形状是一次
    **假的**分歧。

    三条**拒绝**，每一条都对应一类「这条记录不能当结论用」：

    * 某一题没有回答 —— 部分答案集会被读成「这一题后端弃权」，而它其实是整条回执
      不完整（§11：不得静默截断）。
    * 回执里出现了我们没问过的题号 —— 请求与回执对不上的典型形状，而按自己的题号
      逐条取用会让它**静默无害**，直到某一题恰好同号。
    * 选项不在题面的闭集里 / 概率越界 —— 夹取（clamp）会造出一条**看起来正常**的
      记录，而它的取值在题面之外；这正是「一个因为错误原因而绿的测试」的同族。

    这三条都抛 ``ConfigError``：它们说的是**部署在那一头的题面与我们手里的不是
    同一份**，那是一次配置事故的签名，而不是后端对某一段输入弃权。

    ``confidence`` 与 ``probabilities`` **缺失就是 ``None``**，不补 0、不补 1：
    「后端没报」与「后端报了 0」是两条不同的记录。
    """
    asked = list(spec.questions)
    by_id = {question.question_id: question for question in asked}
    unexpected = sorted(set(payload) - set(by_id))
    if unexpected:
        raise ConfigError(
            f"{provider} 的回执里出现了题面之外的题号 {unexpected}；"
            f"本 spec 问的是 {sorted(by_id)} —— 请求与回执对不上"
        )

    answers: list[DecisionAnswer] = []
    for question in asked:
        raw = payload.get(question.question_id)
        if raw is None:
            raise ConfigError(
                f"{provider} 没有回答题号 {question.question_id!r}；"
                "一条只答了一半的回执不能当结论用"
            )
        answers.append(
            _one_answer(raw, question, spec=spec, provider=provider)
        )
    return tuple(answers)


def _one_answer(
    raw: Mapping[str, object],
    question: DecisionQuestion,
    *,
    spec: DecisionSpec,
    provider: str,
) -> DecisionAnswer:
    """一题的翻译。``kind`` 取自**题面**，不取回执 —— 见下。"""
    kind = raw.get("type")
    # 用题面的 kind，不用回执自称的 type：回执说错了类型时，按它走会把一条是否题
    # 的概率读成选择题的分布（两个字段名不同，于是它大概率 KeyError，但那是运气）。
    # 题面是我们自己的，回执是别人给的 —— 判据取自己手里的那一份。
    if question.kind is QuestionKind.YES_NO:
        if kind != "noul":
            raise ConfigError(
                f"{provider} 对是否题 {question.question_id!r} 报的类型是 {kind!r}"
            )
        probability = _probability(raw.get("noul"), question, provider=provider)
        return YesNoAnswer(
            question_id=question.question_id,
            probability=probability,
            confidence=_optional_probability(raw.get("confidence"), question, provider=provider),
        )

    if kind != "choice":
        raise ConfigError(
            f"{provider} 对选择题 {question.question_id!r} 报的类型是 {kind!r}"
        )
    allowed = spec.choices_by_question[question.question_id]
    choice = raw.get("choice")
    if choice not in allowed:
        raise ConfigError(
            f"{provider} 为 {question.question_id!r} 选了 {choice!r}，"
            f"而题面给的取值是 {list(allowed)}"
        )
    distribution = raw.get("probabilities")
    if distribution is None:
        pairs: tuple[tuple[str, float], ...] | None = None
    else:
        if not isinstance(distribution, Mapping):
            raise ConfigError(
                f"{provider} 为 {question.question_id!r} 报的 probabilities 不是映射："
                f"{type(distribution).__name__}"
            )
        unknown = sorted(key for key in distribution if key not in allowed)
        if unknown:
            raise ConfigError(
                f"{provider} 为 {question.question_id!r} 报的分布里有题面之外的选项 "
                f"{unknown}；题面给的是 {list(allowed)}"
            )
        pairs = tuple(
            (key, _probability(distribution.get(key), question, provider=provider))
            for key in allowed
            if key in distribution
        )
    return ChoiceAnswer(
        question_id=question.question_id,
        choice=choice,
        distribution=pairs,
        confidence=_optional_probability(raw.get("confidence"), question, provider=provider),
    )


def _probability(
    value: object, question: DecisionQuestion, *, provider: str
) -> float:
    """一个必须存在的概率值。**越界就拒绝，不夹取。**"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(
            f"{provider} 为 {question.question_id!r} 报的概率不是数字：{value!r}"
        )
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ConfigError(
            f"{provider} 为 {question.question_id!r} 报的概率越界：{number!r}。"
            "夹取会造出一条看起来正常的记录，而它的取值在 [0, 1] 之外。"
        )
    return number


def _optional_probability(
    value: object, question: DecisionQuestion, *, provider: str
) -> float | None:
    """一个**可以缺失**的概率值。``None`` 是「后端没报」，不是 0。"""
    if value is None:
        return None
    return _probability(value, question, provider=provider)


def record_id(
    case: DecisionCase, *, provider: str, decided_at: str
) -> str:
    """一条回执的 id：**输入 + 谁判的 + 什么时候判的**。

    带上 ``decided_at`` 是刻意的：同一个后端对同一份输入重跑一次得到的是**一条新的
    回执**（延迟不同、用量不同），而内容寻址的 id 会把第二次覆盖成第一次 —— §8 要求
    「陈旧的尝试不得覆盖更新的已提交结果」，那件事的前提是两次尝试有不同的 id。

    反过来，同样三个分量必然算出同一个 id，所以一次网络超时后的重试（同一个
    ``decided_at`` 只可能来自同一次调用）不会造出两条记录 —— 幂等由 id 保证，
    不由「写之前先查一下」保证。
    """
    return sha256_text(
        _SEP.join(
            (case.case_id, provider, case.spec.spec_id, case.spec.version,
             case.input_sha256, decided_at)
        )
    )[:_ID_LENGTH]


class DecisionProvider(ABC):
    """一次有界语义判断的后端。Jev 与 Laya 都实现它。

    ## ``configured`` / ``ready`` / ``blocker`` 是三个不同的问题

    ``configured`` 问「**装了没 / 配了没**」（依赖可 import、凭据存在）；
    ``ready`` 问「**能不能真的产出一条记录**」。两者可以不同，而按 ``configured``
    接线是有前车之鉴的：``adapters/mineru_adapter.py`` 里 ``pip install mineru``
    会把「装了没」翻真，而调用点还没校准，于是每一次解析都失败。所以
    **接不接它读的是 ``ready``**，``get_capabilities`` 的两个格子各读一个。

    ``blocker`` 是 ``ready`` 为假时的理由，且必须**已脱敏** —— 它会进
    ``get_capabilities`` 的返回值，而那个接口是给人和 Agent 读状态的
    （§7：客户端拿不到 Serper / Jev 的凭据，一个字都不行）。
    """

    #: 稳定标识。进回执的 ``provider`` 字段，所以它一旦发布就不要改名。
    name: str = "abstract"

    @property
    def configured(self) -> bool:
        """依赖与凭据**在不在**。"""
        return False

    @property
    def ready(self) -> bool:
        """**能不能真的产出一条记录。** 调用方按这一个决定接不接。"""
        return False

    @property
    def blocker(self) -> str:
        """``ready`` 为假时的理由；能用时是空串。不得含凭据。"""
        return ""

    @abstractmethod
    def decide(self, case: DecisionCase, *, mode: DecisionMode) -> DecisionRecord:
        """对一份冻结的输入做一次判定。

        **不抛「不可用」类异常。** 不可用是一种**可审计的结果**（``outcome`` +
        ``reason``），不是一次崩溃：把它做成异常，调用方要么漏接（一次判定失败
        让整条 pipeline 失败，而 §5 明确说「降级的研究结果仍可用」），要么接住
        之后把它记成一句没有字段的字符串。做成回执，它自带 provider / checkpoint
        / 延迟 / 用量 —— 事后能回答「是谁、在什么时候、为什么没给出结论」。

        参数里的 ``mode`` 是**必填**的：同一次判定在影子与辅助两种模式下的可见性
        不同（辅助的答案进研究轨迹，影子的不进），而一个缺省值会让「忘了写模式」
        变成一条静悄悄进了轨迹的影子记录。
        """
        raise NotImplementedError


class DecisionRecordStore(ABC):
    """判定记录的**落点**。

    ## 它为什么还回答「这件工作按哪一份规则跑」

    ``check_scope`` 已经在本端口上了，它答的是「这件工作存在吗、它针对谁」。同一个
    读、同一张 ``work_item`` 表旁边，还住着另外两格事实：``work_item_snapshot``
    里的钉（这件工作编译时按哪一份规则）与 ``scene_snapshot`` 里那个版本号**指称的
    字节**。它们属于同一个问题的另外两半，而这个问题正是本端口存在的理由之一 ——
    **一条记录只能挂在真实的主语上**，而「真实」除了「针对谁」，还包括「按哪一份
    规则判的」。

    它承重在最要紧的那一处：``decide`` 的题面必须从**钉住的那一版**编译
    （§6：Running tasks retain their pinned guidance），否则「钉住」只钉了一个
    号码，判定照旧按盘上此刻那一份跑 —— 而两者分家时，回执上前者写得清清楚楚、
    后者一点痕迹都没有。见 :meth:`pinned_scene`。

    ## 它为什么在端口层，而不在 ``adapters/`` 里直接调

    在此之前，「一条判定发生过」只活在**那一次工具调用的返回值**里
    —— ``adapters/mcp_server/decisions.py`` 当时有一条 ``_NOT_STORED_NOTE``
    逐字记着这件事，而那条常量随本端口的落地一起被取代了（今天那里叫
    ``_NO_RECORD_STORE``，说的是另一件事：**这个实例**没接记录存储）。
    服务一重启它就没有了，换一个客户端它也没有了 —— 而一条判定是**付过费**的。

    ## 幂等键与 ``record_id`` 是**两个**键，而且它有三个分段

    ``record_id`` 是内容寻址的，配方里含 ``decided_at``，所以同一次调用的重放算出
    来的是**另一个** id：内容寻址挡的是「同一件事被记成两条」，挡不住「同一件事被
    做两次」。花钱的是第二次调用，所以真正需要幂等的那一处用的键必须不含时钟
    —— 它是 ``(work_item_id, provider, case_id)``，后两段都由输入与**问谁**派生。

    ``provider`` 那一段是承重的，不是凑数：同一件工作、同一份输入被**两个后端**各
    判一次，是**两次**付费，不是一次重放。少了这一段，「拿 Jev 的结论去和 Laya 比
    一次」会被读成重复付费而整次拒掉 —— 而那两个答案**本来就不是一回事**，合并
    它们比重复付费更坏：一份记录只能有一个 ``provider``，选哪一个都是把另一个后端
    的话记在了别人名下。

    ``case_id`` 里没有 ``provider``（它由题面版本 + 题目 + 文本 + 引用派生），所以
    这一段的加入是必须的 —— 幂等键的每一段都必须是「这次调用与上一次是不是同一件
    事」的一个真实维度。

    ## 一条记录必须挂在**一件工作**上

    ``put_decision`` 收的不是一个自由的 ``subject_ref``，而是一个 ``work_item_id``，
    主体由存储从那个 work item 读出来。这不是绕路：一条可以由调用方任意指定主体的
    记录，就是「通用模型记忆库」的第一块砖 —— 它没有真实主语，于是谁都可以往里写、
    谁都可以从里读。主体是**推导出来的**，不是**声明出来的**。

    ## 证据引用必须在**这一件工作**的可引用范围内

    ``check_scope`` 同时回答三件事：这件工作存在吗、它针对谁、这些引用在不在它的
    范围内。三件事一起答，因为它们出自同一次读。第三条挡的是「B 的判定引用了 A 的
    证据」—— 那种记录上只能看出「它引了两条证据」，读的人无从分辨。§8 要的并发
    隔离里，这一格正是其中的一条。
    """

    @abstractmethod
    def check_scope(self, work_item_id: str, refs: Sequence[str] = ()) -> str:
        """这件工作的主体，以及 ``refs`` 是否都在它的可引用范围内。

        Returns:
            这个 work item 的 ``subject_ref``。

        Raises:
            StoreError: 没有这个 work item，或某条引用不存在 / 不属于它。
        """

    @abstractmethod
    def decision_for_case(
        self, work_item_id: str, provider: str, case_id: str
    ) -> Mapping[str, Any] | None:
        """这件工作下**这个后端**已经判过的这个 case；没判过返回 ``None``。

        这是**付费调用之前**要问的那一句 —— 它决定了「这一次要不要真的去问后端」。
        三个分段一个都不能少：少 ``provider`` 会把「另一个后端判过同一份输入」读成
        重放。
        """

    @abstractmethod
    def put_decision(self, work_item_id: str, receipt: Mapping[str, Any]) -> Mapping[str, Any]:
        """写下一条回执，返回**库里现在的那一行**。

        存的是**回执的投影本身**（调用方真看到的那份形状），不是另一份更"完整"的
        结构 —— 两份形状漂移的那天，按 id 读回来的记录与当时那一份对不上，而两边
        都没有错。

        Raises:
            StoreError: 没有这个 work item；``(work_item_id, provider, case_id)`` 上
                已经有一条**不同** ``record_id`` 的记录（同一个问题被同一个后端判了
                两次，第二次是另一次付费）；或同一个 ``record_id`` 配了不同的内容。
        """

    @abstractmethod
    def get_decision(self, record_id: str) -> Mapping[str, Any] | None:
        """按 id 取一条回执；没有返回 ``None``（不抛）。"""

    @abstractmethod
    def pinned_scene(self, work_item_id: str) -> PinnedScene | None:
        """这件工作编译时**冻住的那份规则**：版本，以及那个版本指称的字节。

        Returns:
            ``None`` = 这件工作**没有钉** —— 它在钉住这个机制出现之前编译的。
            这是与「钉了但字节没留下来」**不同**的一格：前者连版本都没有，
            后者有一个说不出内容的号码。两者都要读得出来，因为它们的处置不同
            （见 ``ports/jobs.py::repin_refusal`` 与 ``scene_files``）。

        ## 为什么是**两格一起**回，而不是「版本」与「字节」两个入口

        分开的两个入口中间那一段，正是这套机制唯一要防的那个窗口：一个进程读到
        版本、另一个进程（或同一进程下一个人）在此之前把内容换掉了，于是手里拿着
        版本 A 的号码和版本 B 的字节，而两份都「没坏」。``SceneFiles`` 自己就是
        为此而生的（版本与内容住在同一个值里，见 ``domain/profile.py``），这里
        照它办。

        ## ``files`` 为 ``None`` **不是**「与盘上现在那份相同」

        那是 ``ports/jobs.py::scene_files`` 逐字写下的同一条：空要读成「本部署没有
        留下这一份内容」。一个把它读成「与盘上相同」的实现，会在一件工作实际按
        旧规则跑的时候报出一份看起来完全正常的回执 —— 而**这正好是本端口这个方法
        存在的理由**。
        """


@dataclass(frozen=True)
class PinnedScene:
    """一件工作编译时冻住的规则：一个版本号，以及它指称的那批字节。

    ``files`` 为 ``None`` 表示**版本在、内容不在**（本部署没有留下这一份）。
    这不是「与盘上现在那一份相同」（见 ``ports/jobs.py::scene_files``）。
    """

    scene_version: str
    files: SceneFiles | None


__all__ = [
    "ChoiceAnswer",
    "DecisionAnswer",
    "DecisionCase",
    "DecisionOption",
    "DecisionPreprocessing",
    "DecisionProvider",
    "DecisionQuestion",
    "DecisionRecord",
    "DecisionRecordStore",
    "DecisionSpec",
    "DecisionUsage",
    "PinnedScene",
    "YesNoAnswer",
    "answers_from_payload",
    "record_id",
]

"""决策端口与它的两个后端（Jev / Laya）：判据。

## 这个文件**没有**跑过真实的判定，而且不假装跑过

``laya`` 装在本机（0.3.4），但那只是**库**：权重不在本机，所以
``LayaDecisionProvider.ready`` 在今天如实是假。``typesafe_sdk`` 也装着（0.7.1），
但 vault 里没有 ``TYPESAFE_API_KEY``，所以 Jev 的线上路径同样没被跑过。两件事都不是
缺陷，而是这两个后端今天的**诚实状态**：§20 —— 缺凭据不得写 PASS。

所以本文件量的是**我们这一侧**：端口上的两个翻译、两个 adapter 的分支、长度边界、
凭据的外泄面，以及我们绑定的那些库名在**装的**库上确实存在。真实的端到端判定在
本机的记录只能是 ``NOT_RUN``。

## 可选依赖不是前提，是一个可以被造出来的事实

``laya`` 与 ``typesafe_sdk`` 都在 ``.[dev]`` **之外**（``pyproject.toml`` 的
``decisions-laya`` / ``decisions-jev``），CI 上两个都不装。所以本文件里没有一条用例
可以把「这台机器恰好装了什么」当成前提：需要依赖在的时候用 :func:`_install_laya`
把它钉住，需要它不在的时候把 ``laya._installed`` 拧成假。一条判据要在**每一种**依赖
状态下都被问到，才算被验过。

2026-09-24 这里真的有两条用例这样红过，而它们单看完全正常：
``LayaDecisionProvider.blocker`` 在依赖缺席时**短路**成一句 import 提示，那两条用例
却从 ``blocker`` 里读「目录不存在」与「必须是绝对路径」—— 在装了 ``laya`` 的本机上
量到的是路径，在 CI 上量到的是依赖，路径那一轴一次都没被问到。

## 替身为什么可以照抄库的算术

``laya`` 的替身（:func:`_install_laya`）**逐行**照抄 ``laya.common.build_sequence``
与 ``render_options``：键读法（``q["t"]`` / ``q["ins"]`` / ``q.get("crit")``）、
``mask_token`` / ``cls_token_id`` / ``sep_token_id`` 的用法、选项的 48 token 上限、
``head_max_len`` 的两段预算、以及 ``room = max(0, max_len - len(ids) - 1)``。

照抄是刻意的：``_room_left`` 自称「量的是库自己的算术」，而它实际量的是
``build_sequence`` 的**返回值**。替身若把算术简化了，那条自称就变成一句对不上事实的
话 —— 而它恰好是本文件里最要紧的一条（「不裁剪」的唯一执行器）。一份简化过的替身会
让「room 是库给的」悄悄变成「room 是我们俩商量出来的」。

这份照抄不会自己漂移：:func:`test_laya_binds_only_to_names_the_installed_library_has`
直接读**装的库的源码**（AST，不 import），把签名、键名、两个默认值钉住。

## Jev 那一侧不必造替身来验回执形状

``typesafe_sdk`` 的**响应模型**是 pydantic 类，import 它只要 pydantic + httpx，
所以 ``.model_dump()`` 那一层用的是**真的** ``ChoiceAnswer`` / ``NoulAnswer``。
不信自己写的替身去对齐对方的数据契约，这件事在这里是免费的。
"""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters import laya as laya_module
from marketops.adapters.jev import REDACTED, JevDecisionProvider
from marketops.adapters.laya import (
    _ABSENT_DIR,
    _LOCAL_MODEL_KEY,
    _REQUIRED_CHECKPOINT_ENTRIES,
    LayaDecisionProvider,
)
from marketops.domain.enums import DecisionMode, DecisionOutcome, DecisionTask, QuestionKind
from marketops.domain.errors import ConfigError
from marketops.ports.decisions import (
    ChoiceAnswer,
    DecisionCase,
    DecisionOption,
    DecisionPreprocessing,
    DecisionQuestion,
    DecisionRecord,
    DecisionSpec,
    DecisionUsage,
    YesNoAnswer,
    answers_from_payload,
    record_id,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 一句「密钥」。取值无所谓，**不得**是真的 —— 它只用来证明它不会出现在记录里。
_A_KEY = "jev-not-a-real-key-0123456789"


# ---------------------------------------------------------------------------
# 题面与用例
# ---------------------------------------------------------------------------
def _spec(*, version: str = "1") -> DecisionSpec:
    """两道题：一道 CHOICE、一道 YES_NO。

    两种 kind 都要有 —— 「回执自称的类型与题面不符」那条只有在两种都存在时才有意义，
    而它正是两个后端最容易一起漂移的地方。
    """
    return DecisionSpec(
        spec_id="page-triage",
        version=version,
        task=DecisionTask.PAGE_TRIAGE,
        questions=(
            DecisionQuestion(
                question_id="kind",
                kind=QuestionKind.CHOICE,
                instructions="Which kind of page is this?",
                options=(
                    DecisionOption("venue", "a page about one club's own ground"),
                    DecisionOption("blog", "an article about the sport in general"),
                ),
            ),
            DecisionQuestion(
                question_id="official",
                kind=QuestionKind.YES_NO,
                instructions="Is this the organisation's own website?",
            ),
        ),
    )


def _case(*, state: str = "A club in Devon, England.", version: str = "1") -> DecisionCase:
    return DecisionCase(case_id="case-1", spec=_spec(version=version), state=state)


def _record(**overrides: Any) -> DecisionRecord:
    """一条最小的合法记录，用来单独打 ``DecisionRecord`` 自己的不变量。"""
    base: dict[str, Any] = {
        "record_id": "r" * 32,
        "case_id": "case-1",
        "spec_id": "page-triage",
        "spec_version": "1",
        "input_sha256": "0" * 64,
        "provider": "laya",
        "model": "",
        "checkpoint": "",
        "preprocessing": DecisionPreprocessing(language=None, route="", input_chars=4),
        "outcome": DecisionOutcome.ANSWERED,
        "answers": (YesNoAnswer("official", 0.5, None),),
        "reason": "",
        "latency_ms": 1,
        "usage": DecisionUsage(None, None, False),
        "decided_at": "2026-01-01T00:00:00+00:00",
        "mode": DecisionMode.SHADOW,
    }
    base.update(overrides)
    return DecisionRecord(**base)


def _choice_reply(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": "choice",
        "choice": "venue",
        "probabilities": {"venue": 0.7, "blog": 0.3},
        "confidence": 0.4,
    }
    payload.update(overrides)
    return payload


def _noul_reply(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"type": "noul", "noul": 0.81, "confidence": 0.81}
    payload.update(overrides)
    return payload


def _both(**overrides: object) -> dict[str, dict[str, object]]:
    payload: dict[str, Any] = {"kind": _choice_reply(), "official": _noul_reply()}
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# 端口：题面的那一半
# ---------------------------------------------------------------------------
def test_the_wire_shape_is_one_translation_and_the_hosted_sdk_accepts_it() -> None:
    """``as_questions`` 的输出直接过**对方**的校验器。

    这一条比任何键名断言都强：用的是 ``typesafe_sdk`` 自己的 pydantic 模型（题面模型
    是 ``extra="forbid"`` 的）。形状若与它不符，我们在通话之前就知道 —— 而不是在一条
    400 后面猜「是不是字段名错了」。

    ``YES_NO`` 不带 ``criteria`` 也在这里被钉住：它不是风格，是那条模型上
    ``criteria`` 的类型（``true`` / ``false`` 两个描述），而我们没有那两个描述可给。
    """
    typesafe = pytest.importorskip(
        "typesafe_sdk", reason="装上 SDK 才能用对方的校验器量我们的题面"
    )
    from pydantic import TypeAdapter, ValidationError

    questions = _spec().as_questions()
    TypeAdapter(typesafe.Questions).validate_python(questions)

    assert questions["official"] == {
        "type": "noul",
        "instructions": "Is this the organisation's own website?",
    }, "是否题多带了字段 —— 线上形状是两个后端共用的那一份"

    # 正对照：这个校验器真的在查东西。一个选择题少了 criteria 必须过不去，否则上面
    # 那句「过得了对方的校验器」在对方关掉校验时同样成立。
    broken = _spec().as_questions()
    del broken["kind"]["criteria"]
    with pytest.raises(ValidationError):
        TypeAdapter(typesafe.Questions).validate_python(broken)


# ---------------------------------------------------------------------------
# 端口：回执的那一半
# ---------------------------------------------------------------------------
def test_a_reply_that_skips_a_question_is_refused() -> None:
    """只答一半的回执会被读成「那一题后端弃权」—— 而它其实是整条回执不完整。"""
    payload = {"kind": _choice_reply()}
    with pytest.raises(ConfigError) as exc:
        answers_from_payload(payload, _spec(), provider="laya")
    assert "official" in str(exc.value)


def test_a_reply_that_answers_a_question_we_never_asked_is_refused() -> None:
    """按自己的题号逐条取用会让这一条**静默无害**，直到某一题恰好同号。"""
    with pytest.raises(ConfigError) as exc:
        answers_from_payload(_both(invented=_noul_reply()), _spec(), provider="laya")
    assert "invented" in str(exc.value)


def test_an_option_outside_the_spec_is_refused_and_not_clamped() -> None:
    payload = _both(kind=_choice_reply(choice="maybe"))
    with pytest.raises(ConfigError) as exc:
        answers_from_payload(payload, _spec(), provider="laya")
    assert "venue" in str(exc.value) and "blog" in str(exc.value)


def test_an_option_that_was_never_given_a_probability_is_not_filled_in() -> None:
    """分布里少了题面的某一项时，留下来的那一项仍是**实名**的，不是被补齐的。"""
    answers = answers_from_payload(
        _both(kind=_choice_reply(probabilities={"venue": 1.0})), _spec(), provider="laya"
    )
    first = answers[0]
    assert isinstance(first, ChoiceAnswer)
    assert first.distribution == (("venue", 1.0),), "缺的那一项不得被补成 0"


def test_a_probability_outside_the_unit_interval_is_refused_and_not_clamped() -> None:
    """夹取会造出一条**看起来正常**的记录，而它的取值在题面之外。"""
    payload = _both(kind=_choice_reply(probabilities={"venue": 1.4, "blog": -0.4}))
    with pytest.raises(ConfigError) as exc:
        answers_from_payload(payload, _spec(), provider="laya")
    assert "越界" in str(exc.value)


def test_a_probability_that_is_not_a_number_is_refused() -> None:
    """``True`` 是 ``int`` 的子类 —— 不单独拦下它，``p(yes) = True`` 会被读成 1.0。"""
    payload = _both(official=_noul_reply(noul=True))
    with pytest.raises(ConfigError) as exc:
        answers_from_payload(payload, _spec(), provider="laya")
    assert "不是数字" in str(exc.value)


def test_the_question_kind_comes_from_the_spec_not_from_the_reply() -> None:
    """回执说错了类型时按**题面**走 —— 判据取自己手里的那一份。"""
    payload = _both(official=_choice_reply())
    with pytest.raises(ConfigError) as exc:
        answers_from_payload(payload, _spec(), provider="laya")
    assert "official" in str(exc.value)


def test_a_missing_confidence_is_none_and_a_reported_zero_is_zero() -> None:
    """「后端没报」与「后端报了 0」是两条不同的记录。"""
    missing = answers_from_payload(
        _both(kind=_choice_reply(confidence=None), official=_noul_reply(confidence=None)),
        _spec(),
        provider="laya",
    )
    assert [answer.confidence for answer in missing] == [None, None]

    zero = answers_from_payload(
        _both(kind=_choice_reply(confidence=0.0), official=_noul_reply(confidence=0.0)),
        _spec(),
        provider="laya",
    )
    assert [answer.confidence for answer in zero] == [0.0, 0.0]


def test_the_distribution_follows_the_specs_option_order() -> None:
    """分布按题面给的顺序重排 —— 两个后端的字典序不可控，逐字记下来就不可比。"""
    answers = answers_from_payload(
        _both(kind=_choice_reply(probabilities={"blog": 0.3, "venue": 0.7})),
        _spec(),
        provider="laya",
    )
    first = answers[0]
    assert isinstance(first, ChoiceAnswer)
    assert first.distribution == (("venue", 0.7), ("blog", 0.3))

    # 后半截与前半截是两件事：分布缺失（后端没报）与分布为空（后端报了空）不同。
    without = answers_from_payload(
        _both(kind=_choice_reply(probabilities=None)), _spec(), provider="laya"
    )[0]
    assert isinstance(without, ChoiceAnswer)
    assert without.distribution is None


def test_a_yes_no_answer_has_no_label() -> None:
    """端口只承载 ``p(yes)``。折成 ``"yes"``/``"no"`` 需要一个没人授权过的阈值。"""
    answer = answers_from_payload(_both(), _spec(), provider="laya")[1]
    assert isinstance(answer, YesNoAnswer)
    assert answer.probability == 0.81
    assert not hasattr(answer, "label")


# ---------------------------------------------------------------------------
# 端口：记录与 id
# ---------------------------------------------------------------------------
def test_a_record_cannot_claim_answered_without_an_answer() -> None:
    with pytest.raises(ConfigError):
        _record(outcome=DecisionOutcome.ANSWERED, answers=(), reason="")


def test_a_non_answer_without_a_reason_is_refused() -> None:
    """一个没有理由的弃权，在报告里与一次崩溃无法区分。"""
    with pytest.raises(ConfigError):
        _record(outcome=DecisionOutcome.ABSTAINED, answers=(), reason="   ")
    kept = _record(outcome=DecisionOutcome.ABSTAINED, answers=(), reason="读不懂")
    assert kept.answered is False


def test_the_input_hash_cannot_be_passed_in() -> None:
    """它是 property 而不是字段 —— 一个可以传进来的哈希就能与文本不一致。"""
    with pytest.raises(TypeError):
        DecisionCase(
            case_id="case-1", spec=_spec(), state="x", input_sha256="0" * 64
        )  # type: ignore[call-arg]


def test_the_input_hash_moves_with_the_text_and_with_the_spec_version() -> None:
    base = _case().input_sha256
    assert base != _case(state="A different club in Devon.").input_sha256
    assert base != _case(version="2").input_sha256


def test_the_record_id_is_stable_within_one_call_and_distinct_across_calls() -> None:
    """幂等由 id 保证，不由「写之前先查一下」保证。"""
    case = _case()
    first = record_id(case, provider="laya", decided_at="2026-01-01T00:00:00+00:00")
    assert first == record_id(case, provider="laya", decided_at="2026-01-01T00:00:00+00:00")
    assert first != record_id(case, provider="laya", decided_at="2026-01-01T00:00:01+00:00")
    assert first != record_id(case, provider="jev", decided_at="2026-01-01T00:00:00+00:00")


# ---------------------------------------------------------------------------
# Laya：替身
# ---------------------------------------------------------------------------
#: 替身装进 ``sys.modules["laya"]`` 的那一组 key。真实库的 ``DEFAULT_MODELS`` 有哪几个
#: key 由 :func:`test_laya_binds_only_to_names_the_installed_library_has` 钉住；这里
#: 只是让 adapter 那个 ``for key in laya.DEFAULT_MODELS`` 有东西可迭代。
_STUB_MODEL_KEYS = ("english", "multilingual", "typed-decisions")

#: **库**把英文路由到的那个 key，逐字写死。
#:
#: 不能写成 ``_LOCAL_MODEL_KEY`` —— 那样替身会跟着我们的常量走，于是「英文输入被
#: 本地 checkpoint 接住」这条判据变成一句自洽的废话：常量搬家时它照样绿。判断
#: 「英文走哪一个 key」的是**库**（``Router`` 的缺省值），不是我们。
_LIBRARY_ENGLISH_KEY = "english"


class _Tokenizer:
    """一个**确定性**的 tokenizer：一个字符一个 token。

    真实 tokenizer 被 ``build_sequence`` 读的只有 ``mask_token`` /
    ``mask_token_id`` / ``cls_token_id`` / ``sep_token_id`` 四个属性与 ``__call__``，
    所以这里只有这四个。不需要 ``pad_token_id``：它被 ``collate_items`` 读，而那个
    在 ``system_one`` 里 —— 替身没有实现前向，那需要 ``torch``，是本文件刻意不碰的。
    """

    mask_token = "[MASK]"
    mask_token_id = 1
    cls_token_id = 2
    sep_token_id = 3

    def __call__(self, text: object, add_special_tokens: bool = False) -> dict[str, list[int]]:
        return {"input_ids": [0] * len(str(text))}


class _LayaStub:
    """``laya`` 的替身，外加它的观察面。

    只有 ``laya`` 会读的那几处被照抄：题面的**内部**键（``t`` / ``ins`` / ``crit``）、
    ``build_sequence`` 的算术、``render_options`` 的读法。其余（权重、前向）都不存在
    —— 那些在本机跑不了，也不该假造。
    """

    def __init__(
        self,
        *,
        route_model: str = _LIBRARY_ENGLISH_KEY,
        route_reason: str = "English Latin text",
        language: str = "en",
        reply: dict[str, Any] | None = None,
        route_exc: Exception | None = None,
        load_exc: Exception | None = None,
        infer_exc: Exception | None = None,
        max_len: int = 512,
        head_max_len: int = 192,
    ) -> None:
        self.route_model = route_model
        self.route_reason = route_reason
        self.language = language
        self.reply = reply if reply is not None else _laya_payload()
        self.route_exc = route_exc
        self.load_exc = load_exc
        self.infer_exc = infer_exc
        self.max_len = max_len
        self.head_max_len = head_max_len
        self.tok = _Tokenizer()
        # 观察面
        self.routers: list[dict[str, Any]] = []
        self.loaded: list[str] = []
        self.rendered: list[dict[str, Any]] = []
        self.probes: list[str] = []
        self.rooms: list[int] = []
        self.system_one_calls = 0

    @property
    def cfg(self) -> dict[str, int]:
        return {"max_len": self.max_len, "head_max_len": self.head_max_len}

    def system_one(self, state: str, questions: dict[str, Any]) -> dict[str, Any]:
        self.system_one_calls += 1
        if self.infer_exc is not None:
            raise self.infer_exc
        return self.reply


def _module(name: str, owner: Any = None) -> types.ModuleType:
    """一个带 ``__spec__`` 的假模块。

    ``__spec__`` 是必需的：``configured`` 走 ``importlib.util.find_spec``，而那个函数
    对**已在 ``sys.modules`` 里的名字**直接返回它的 ``__spec__``（没有就返回 ``None``，
    于是 ``configured`` 静默变假）。给一个 spec 让「装了库」在测试里成为可造的事实 ——
    否则这一组用例只能在本机（恰好装了 ``laya``）上跑，在别的机器上量到别的东西。
    """
    module = types.ModuleType(name)
    module.__spec__ = importlib.machinery.ModuleSpec(name, None)
    module._owner = owner  # type: ignore[attr-defined]
    return module


def _install_laya(monkeypatch: pytest.MonkeyPatch, **knobs: Any) -> _LayaStub:
    """把替身装进 ``sys.modules``，并让 ``find_spec`` 也看见它。"""
    stub = _LayaStub(**knobs)

    def render_criterion(value: object) -> str:
        """``laya.common.render_criterion``：字符串原样，其余紧凑 JSON。"""
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "), default=str)

    def render_options(q: dict[str, Any]) -> list[str]:
        """``laya.common.render_options``：逐支照抄。"""
        t, crit = q["t"], q.get("crit")
        if t == "choice":
            # 只有 None/"" 是「没有描述」；0 与 False 是合法的描述值。
            return [
                k if v is None or v == "" else "%s: %s" % (k, render_criterion(v))
                for k, v in crit.items()
            ]
        if t == "score":
            return ["level %d: %s" % (i, render_criterion(c)) for i, c in enumerate(crit)]
        crit = crit or {}
        false_crit, true_crit = crit.get("false"), crit.get("true")
        return [
            "false: "
            + (
                render_criterion(false_crit)
                if false_crit not in (None, "")
                else "no, the statement does not hold"
            ),
            "true: "
            + (
                render_criterion(true_crit)
                if true_crit not in (None, "")
                else "yes, the statement holds"
            ),
        ]

    def serialize_state(state: object) -> str:
        return state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)

    def build_sequence(
        tok: Any,
        state: object,
        q: dict[str, Any],
        max_len: int = 512,
        head_max_len: int = 192,
        option_order: list[int] | None = None,
        truncate_left: bool = False,
    ) -> tuple[list[int], list[int]]:
        """``laya.common.build_sequence`` 的替身：**同算术、同 room 配方**。

        三处刻意照抄，各自都有一类漂移要挡：

        * ``q["t"]`` / ``q["ins"]`` / ``q.get("crit")`` —— 键名漂了这里 ``KeyError``，
          而不是给一个偏小的 room（那个会让「装得下」的结论对真正要问的那道题不成立，
          而截断依然静默）；
        * ``head_max_len`` 的两段预算与选项的 48 token 上限 —— 它们决定 head 有多长，
          也就决定 room；
        * ``room = max(0, max_len - len(ids) - 1)`` 与末尾的 ``ids[:max_len]`` ——
          ``_room_left`` 的公式在数学上依赖这两行。
        """
        stub.rendered.append(dict(q))
        stub.probes.append(serialize_state(state))

        mask_tok = tok.mask_token
        opts = render_options(q)
        order = option_order if option_order is not None else list(range(len(opts)))
        ins = str(q["ins"]).replace(mask_tok, " ")
        head_ids = tok("%s question: %s" % (q["t"], ins), add_special_tokens=False)[
            "input_ids"
        ]
        opt_ids = []
        for i in order:
            opt_ids.append(
                [tok.mask_token_id]
                + tok(" " + opts[i].replace(mask_tok, " "), add_special_tokens=False)[
                    "input_ids"
                ][:48]
            )
        opt_budget = head_max_len - sum(len(o) for o in opt_ids)
        if opt_budget < 16:
            per = max(4, (head_max_len - 16) // max(1, len(opt_ids)))
            opt_ids = [o[:per] for o in opt_ids]
            opt_budget = head_max_len - sum(len(o) for o in opt_ids)
        head_ids = head_ids[: max(8, opt_budget)]
        ids = [tok.cls_token_id] + head_ids + [tok.sep_token_id]
        markers = []
        for o in opt_ids:
            markers.append(len(ids))
            ids.extend(o)
        ids.append(tok.sep_token_id)
        room = max(0, max_len - len(ids) - 1)
        stub.rooms.append(room)
        st = tok(serialize_state(state).replace(mask_tok, " "), add_special_tokens=False)[
            "input_ids"
        ]
        st = st[-room:] if truncate_left else st[:room]
        ids = ids + st + [tok.sep_token_id]
        return ids[:max_len], [m for m in markers if m < max_len]

    class Router:
        def __init__(
            self,
            models: dict[str, Any] | None = None,
            device: str | None = None,
            token: str | None = None,
            max_loaded: int = 1,
            default: str = "english",
            auto_task_detection: bool = False,
            standalone_repos: bool = False,
            preload: bool = False,
        ) -> None:
            stub.routers.append(
                {"models": dict(models or {}), "max_loaded": max_loaded, "default": default}
            )

        def route(
            self, state: object, questions: dict[str, Any] | None = None
        ) -> dict[str, str]:
            if stub.route_exc is not None:
                raise stub.route_exc
            return {"model": stub.route_model, "reason": stub.route_reason}

        def load(self, name: str) -> _LayaStub:
            stub.loaded.append(name)
            if stub.load_exc is not None:
                raise stub.load_exc
            return stub

    def detect_language(state: str) -> dict[str, Any]:
        return {"language": stub.language, "script": "latin", "is_english": True}

    package = _module("laya")
    package.Router = Router  # type: ignore[attr-defined]
    package.detect_language = detect_language  # type: ignore[attr-defined]
    package.DEFAULT_MODELS = {  # type: ignore[attr-defined]
        "english": ("convaiinnovations/laya", None),
        "multilingual": ("convaiinnovations/laya", "multilingual"),
        "typed-decisions": ("convaiinnovations/laya", "typed-decisions"),
    }
    common = _module("laya.common", package)
    common.build_sequence = build_sequence  # type: ignore[attr-defined]
    common.render_options = render_options  # type: ignore[attr-defined]
    package.common = common  # type: ignore[attr-defined]

    for module in (package, common):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    return stub


def _laya_payload() -> dict[str, Any]:
    """一条**与真实回执同形**的回执（键名抄自 ``laya.agent.Agent.system_one``）。"""
    return {
        "model": "laya-rl-agent",
        "answers": {
            "kind": {
                "type": "choice",
                "choice": "venue",
                "probabilities": {"venue": 0.91, "blog": 0.09},
                "confidence": 0.82,
                "action": {"act_probability": 0.5},
            },
            "official": {
                "type": "noul",
                "noul": 0.77,
                "confidence": 0.77,
                "action": {"act_probability": 0.5},
            },
        },
        "usage": {"input_tokens": 42, "output_tokens": 0},
    }


def _checkpoint(root: Path, *, omit: tuple[str, ...] = ()) -> Path:
    """一个**完整**的本地 checkpoint 目录，可刻意缺几样。

    ``tokenizer`` / ``encoder`` 是目录、另两样是文件 —— 这不是随意的：
    ``_REQUIRED_CHECKPOINT_ENTRIES`` 存在的理由正是这四样缺任何一样都会让库去
    HuggingFace 取，所以「在不在」必须按各自的类型真的判得出来。
    """
    root.mkdir(parents=True, exist_ok=True)
    for entry in _REQUIRED_CHECKPOINT_ENTRIES:
        if entry in omit:
            continue
        target = root / entry
        if target.suffix:
            target.write_text("{}", encoding="utf-8")
        else:
            target.mkdir(parents=True, exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# Laya：能力报告
# ---------------------------------------------------------------------------
def test_the_capability_report_does_not_load_the_backends() -> None:
    """**能力报告路径不得 import 任何一个后端。**

    实测 ``import laya`` 要 3.63 秒并把 ``torch`` 拉进 ``sys.modules``，而
    ``get_capabilities`` 是一条**每条工具调用都可能读**的状态面 —— 让它付这个代价，
    等于给一个只想知道「后端接上了没」的调用加上一次完整的 torch 导入。

    在**子进程**里跑，因为这条判据问的是「import 之后 ``sys.modules`` 里有什么」——
    在当前进程里问，答案会被这个文件自己 import 过的东西污染。变异（把 ``import laya``
    挪到模块层）是一个看起来完全无害的重构。
    """
    script = (
        "import json, sys\n"
        "from marketops.adapters.jev import JevDecisionProvider\n"
        "from marketops.adapters.laya import LayaDecisionProvider\n"
        "for provider in (LayaDecisionProvider('/nonexistent'), JevDecisionProvider('k')):\n"
        "    provider.configured, provider.ready, provider.blocker\n"
        "loaded = [m for m in ('torch', 'laya', 'typesafe_sdk') if m in sys.modules]\n"
        "print(json.dumps(sorted(loaded)))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    loaded = json.loads(proc.stdout.strip().splitlines()[-1])
    assert loaded == [], (
        f"读一次能力报告就把 {loaded} 拉进了 sys.modules —— "
        "configured 必须走 find_spec，它只查路径、不执行模块"
    )


@pytest.mark.parametrize("missing", _REQUIRED_CHECKPOINT_ENTRIES)
def test_every_one_of_the_four_entries_is_load_bearing(
    missing: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """四样缺任何一样，``ready`` 都是假，而且缺的那一样叫得出名字。

    ``ready`` 要求四件齐全而不是「目录存在」，因为库有**四条**静默去 HuggingFace
    取东西的路：目录不在、``tokenizer/`` 不在、``encoder/`` 不在、
    ``rl_agent_config.json`` 不在。任何一条被走上都是几百 MB 级的隐式下载，落在
    ``HF_HOME``（默认在 HOME 下），而宿主策略明确禁止在 HOME 下建大缓存。
    """
    _install_laya(monkeypatch)
    complete = LayaDecisionProvider(str(_checkpoint(tmp_path / "ckpt")))
    assert complete.missing_entries == ()
    assert complete.ready, "四样齐全却不 ready —— 下面那条对照就没有意义了"

    defective = LayaDecisionProvider(str(_checkpoint(tmp_path / "defective", omit=(missing,))))
    assert defective.missing_entries == (missing,)
    assert not defective.ready
    assert missing in defective.blocker


def test_a_missing_checkpoint_directory_is_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """目录不在 ⇒ ``ready`` 为假 + 一句话，而**不是一次崩溃**。

    依赖那一轴先被钉住（:func:`_install_laya`）：不钉住的话，在一台没装 ``laya`` 的
    机器上 ``ready`` 同样是假、``blocker`` 说的却是依赖，于是这条用例量到的成了另一
    件事，路径那一轴一次都没被问到。
    """
    _install_laya(monkeypatch)
    nowhere = str(tmp_path / "nowhere")
    provider = LayaDecisionProvider(nowhere)
    assert provider.configured, "对照：依赖那一轴被判成「在」"
    assert provider.missing_entries == (f"<{nowhere!r} 不是一个目录>",)
    assert not provider.ready
    assert "nowhere" in provider.blocker


def test_a_relative_checkpoint_path_is_refused_even_when_it_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相对路径在 ``Agent`` 眼里不是本地目录，是 hub 上的一个 repo id（⇒ 下载）。

    正对照是**这个目录真的存在**：``is_dir()`` 会为真，所以拦下它的是「必须绝对」
    那一条，而不是「找不到」。少了这一半，这条用例在一个「要求目录存在」的实现上
    同样绿，而那个实现挡不住下载。

    第二个正对照是**依赖那一轴被钉住**：它缺席时 ``blocker`` 短路成一句 import 提示，
    于是这条用例在 CI 上报的是「没装 laya」，而不是「相对路径被拒」。
    """
    _install_laya(monkeypatch)
    _checkpoint(tmp_path / "ckpt")
    monkeypatch.chdir(tmp_path)
    assert (tmp_path / "ckpt").is_dir()

    provider = LayaDecisionProvider("ckpt")
    assert provider.configured, "对照：依赖那一轴被判成「在」"
    assert provider.missing_entries == ("<'ckpt' 不是绝对路径>",)
    assert not provider.ready
    assert "绝对路径" in provider.blocker


def test_the_dependency_axis_does_not_decide_the_path_axis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两条轴互不决定：``configured`` 问依赖在不在，``missing_entries`` 问权重全不全。

    这条断的是**后者不随前者变**。做法是把依赖那一轴拧到「不在」（CI 的常态，也是
    任何一台没装可选依赖的机器的常态），再看权重那一轴给出的结论有没有跟着变 ——
    同一份完整目录，两种依赖状态，``missing_entries`` 都是空元组。

    值得单独一条，是因为 ``blocker`` 把两轴压成一句话而在依赖缺席时短路：只看
    ``blocker`` 的话，「路径对、依赖不在」与「路径错、依赖不在」长得一模一样，
    排查方向会被引向 pip 安装。
    """
    complete = _checkpoint(tmp_path / "ckpt")
    _install_laya(monkeypatch)
    assert LayaDecisionProvider(str(complete)).missing_entries == ()

    monkeypatch.setattr(laya_module, "_installed", lambda _name: False)
    provider = LayaDecisionProvider(str(complete))
    assert not provider.configured, "拧到「不在」的那一轴没生效"
    assert not provider.ready
    assert provider.missing_entries == (), "依赖不在，把权重那一轴的结论也带歪了"
    assert "decisions-laya" in provider.blocker, "这一条说的是依赖，不是权重"


def test_the_other_checkpoints_are_pointed_at_paths_that_do_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有本地权重的 key 指向一个**不存在**的路径，而不是同一个目录。

    两个后果都是要防的：指向同一个目录 ⇒ 英文权重去答非英文（高置信的错答）；指向
    一个存在的路径 ⇒ 某次漏过闸门的 ``load`` 会安静地建起一个不该建的模型。这里断的
    是第二件，第一件由 ``test_a_non_english_route_abstains_...`` 断。
    """
    stub = _install_laya(monkeypatch)
    checkpoint = _checkpoint(tmp_path / "ckpt")
    LayaDecisionProvider(str(checkpoint)).decide(_case(), mode=DecisionMode.SHADOW)

    assert len(stub.routers) == 1, "Router 建了几次"
    models = stub.routers[0]["models"]
    assert set(models) == set(_STUB_MODEL_KEYS), "每一个 key 都要有一个路径，不能漏"
    assert models[_LOCAL_MODEL_KEY] == str(checkpoint)
    for key, value in models.items():
        if key == _LOCAL_MODEL_KEY:
            continue
        assert _ABSENT_DIR in value, f"{key} 指向了本地权重那种路径"
        assert not Path(value).exists(), f"{key} 指向了盘上真实存在的东西：{value}"


def test_one_router_is_built_for_the_whole_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一个 421M 的模型装两遍不是慢，是 OOM —— 而这台机器上还有别的并发工作。"""
    stub = _install_laya(monkeypatch)
    provider = LayaDecisionProvider(str(_checkpoint(tmp_path / "ckpt")))
    provider.decide(_case(), mode=DecisionMode.SHADOW)
    provider.decide(_case(), mode=DecisionMode.SHADOW)
    assert len(stub.routers) == 1


# ---------------------------------------------------------------------------
# Laya：分支
# ---------------------------------------------------------------------------
def test_an_english_route_answers_and_records_what_the_library_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_laya(monkeypatch)
    checkpoint = _checkpoint(tmp_path / "ckpt")
    record = LayaDecisionProvider(str(checkpoint)).decide(_case(), mode=DecisionMode.SHADOW)

    assert record.outcome is DecisionOutcome.ANSWERED
    assert record.answered is True
    assert record.provider == "laya"
    assert record.checkpoint == str(checkpoint)
    assert record.model == "laya-rl-agent", "回执自称的那一个要逐字记下来"
    assert record.preprocessing.language == "en"
    assert record.preprocessing.route.startswith(f"{_LOCAL_MODEL_KEY}: ")
    assert record.preprocessing.input_chars == len(_case().state)
    assert record.usage.input_tokens == 42
    assert record.usage.billed is False, "本地进程没有外部计量成本"
    assert [answer.question_id for answer in record.answers] == ["kind", "official"]

    kind, official = record.answers
    assert isinstance(kind, ChoiceAnswer) and kind.choice == "venue"
    assert isinstance(official, YesNoAnswer) and official.probability == 0.77


def test_a_non_english_route_abstains_with_the_librarys_own_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """路由选中的不是我本地的 checkpoint ⇒ 弃权，而不是拿英文权重去答。

    英文 checkpoint 对非英文不是「退化」而是**崩塌**，而且它同时报高置信（库里记着
    MASSIVE 印地语 0.100 / 韩语 0.103，随机基线 0.050，ECE 0.855）。一条高置信的错答
    会一路走进证据，而报告上它与一次正确的判定长得一模一样。

    理由里带的是**库自己那句**（``RouteDecision.reason``），不是我们重写的 ——
    判断权在库里，我们只执行它的结论。
    """
    reason = "non-Latin script (Devanagari, 100% of letters); english cannot read it"
    stub = _install_laya(monkeypatch, route_model="multilingual", route_reason=reason)
    provider = LayaDecisionProvider(str(_checkpoint(tmp_path / "ckpt")))
    record = provider.decide(_case(), mode=DecisionMode.SHADOW)

    assert record.outcome is DecisionOutcome.ABSTAINED
    assert record.answered is False
    assert record.answers == ()
    assert reason in record.reason, "库自己的那句话必须逐字留在理由里"
    assert record.preprocessing.route == f"multilingual: {reason}"
    assert stub.loaded == [], "弃权之后不该再去加载任何权重"
    assert stub.system_one_calls == 0


def test_a_load_failure_is_unavailable_and_names_the_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """装载失败是部署问题 ⇒ ``UNAVAILABLE``（换一段输入不会好转）。"""
    stub = _install_laya(monkeypatch, load_exc=FileNotFoundError("no such directory"))
    provider = LayaDecisionProvider(str(_checkpoint(tmp_path / "ckpt")))
    record = provider.decide(_case(), mode=DecisionMode.SHADOW)

    assert record.outcome is DecisionOutcome.UNAVAILABLE
    assert "FileNotFoundError" in record.reason and "no such directory" in record.reason
    assert stub.loaded == [_LOCAL_MODEL_KEY]


def test_a_library_exception_becomes_a_record_and_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不可用是一种**可审计的结果**，不是一次崩溃。

    做成异常，调用方要么漏接（一次判定失败让整条 pipeline 失败，而 §5 明确说降级的
    研究结果仍可用），要么接住之后记成一句没有字段的字符串。做成回执，它自带
    provider / checkpoint / 延迟 / 用量。
    """
    _install_laya(monkeypatch, infer_exc=RuntimeError("CUDA out of memory"))
    provider = LayaDecisionProvider(str(_checkpoint(tmp_path / "ckpt")))
    record = provider.decide(_case(), mode=DecisionMode.SHADOW)

    assert record.outcome is DecisionOutcome.UNAVAILABLE
    assert record.answers == ()
    assert "RuntimeError" in record.reason and "CUDA out of memory" in record.reason
    assert record.record_id, "没做成的那一次也要算得出 id"


def test_a_laya_reply_that_contradicts_the_spec_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """部署在那一头的题面与我们手里的不是同一份 —— 那是一次配置事故的签名。"""
    payload = _laya_payload()
    payload["answers"]["kind"]["choice"] = "maybe"
    _install_laya(monkeypatch, reply=payload)
    provider = LayaDecisionProvider(str(_checkpoint(tmp_path / "ckpt")))
    record = provider.decide(_case(), mode=DecisionMode.SHADOW)

    assert record.outcome is DecisionOutcome.UNAVAILABLE
    assert "题面不符" in record.reason


def test_a_reply_with_no_answers_at_all_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``payload.get("answers") or {}`` —— 空回执要落成一条说明白的记录，不是 KeyError。"""
    _install_laya(monkeypatch, reply={"model": "laya-rl-agent"})
    provider = LayaDecisionProvider(str(_checkpoint(tmp_path / "ckpt")))
    record = provider.decide(_case(), mode=DecisionMode.SHADOW)

    assert record.outcome is DecisionOutcome.UNAVAILABLE
    assert "kind" in record.reason


def test_an_unusable_provider_still_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没配 checkpoint 时给出一条 ``UNAVAILABLE`` 记录，理由就是 ``blocker``。"""
    stub = _install_laya(monkeypatch)
    provider = LayaDecisionProvider(None)
    record = provider.decide(_case(), mode=DecisionMode.SHADOW)

    assert record.outcome is DecisionOutcome.UNAVAILABLE
    assert record.reason == provider.blocker
    assert stub.routers == [] and stub.system_one_calls == 0


# ---------------------------------------------------------------------------
# Laya：长度（不裁剪的唯一执行器）
# ---------------------------------------------------------------------------
def test_the_room_is_the_librarys_own_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**最后一段装得下的输入被回答，多一个 token 就弃权。**

    边界值不是这里算出来的，是替身在 ``build_sequence`` 里报出来的，而 ``_room_left``
    拿的是**库那个函数**的结果 —— 所以这条量的是「我们用的就是它的数」，不是「我们的
    算术自洽」。把 ``_room_left`` 改成返回 ``max_len``（一个不复述头部长度的实现）会
    当场红。
    """
    stub = _install_laya(monkeypatch, max_len=256)
    provider = LayaDecisionProvider(str(_checkpoint(tmp_path / "ckpt")))
    provider.decide(_case(state="aaaa"), mode=DecisionMode.SHADOW)
    room = min(stub.rooms)

    assert room > 4, "这一组的前提是窗口还没被题面吃满，请调小题目长度或调大 max_len"
    fits = provider.decide(_case(state="a" * room), mode=DecisionMode.SHADOW)
    over = provider.decide(_case(state="a" * (room + 1)), mode=DecisionMode.SHADOW)

    assert fits.outcome is DecisionOutcome.ANSWERED
    assert over.outcome is DecisionOutcome.ABSTAINED
    assert str(room + 1) in over.reason and str(room) in over.reason, (
        "弃权理由要同时说得出「这次有多长」与「窗口留了多少」"
    )


def test_the_tightest_question_decides_whether_the_state_fits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取各题里**最紧**的那一个余量。

    ``system_one`` 一题一条序列、各题头部长短不同，所以一段 state 可能放得进 A 题
    却放不进 B 题。按最大的那个接线，B 题会被库静默裁剪，而记录上只有原文 ——
    这正是 §11 禁止的那件事，且它在报告上完全看不出来。
    """
    stub = _install_laya(monkeypatch, max_len=256)
    provider = LayaDecisionProvider(str(_checkpoint(tmp_path / "ckpt")))
    provider.decide(_case(state="aaaa"), mode=DecisionMode.SHADOW)
    tight, loose = min(stub.rooms), max(stub.rooms)

    assert tight < loose, "两道题的头部一样长时这条判据测不出东西，请改题目文本"
    record = provider.decide(_case(state="a" * (tight + 1)), mode=DecisionMode.SHADOW)
    assert record.outcome is DecisionOutcome.ABSTAINED, (
        f"最紧的那道题只留得下 {tight} 个 token，却放行了 {tight + 1} 个"
    )
    assert str(tight) in record.reason, "理由要给的是最紧的那一个数"


def test_a_question_that_fills_the_window_is_unavailable_not_abstained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两种「装不下」的处置相反，所以不能合成一个 outcome。

    * **题面与选项自己占满窗口** ⇒ 每一条输入都会失败（只取决于题面与这批权重）
      ⇒ ``UNAVAILABLE``，要去改题面或换权重；
    * **这一段 state 太长** ⇒ 换一段证据还能跑 ⇒ ``ABSTAINED``。

    压成一个之后，「这批权重配不上这个题面」与「这份证据太长」在运维界面上不可区分。
    """
    stub = _install_laya(monkeypatch, max_len=100)
    provider = LayaDecisionProvider(str(_checkpoint(tmp_path / "ckpt")))
    record = provider.decide(_case(state="a"), mode=DecisionMode.SHADOW)

    assert record.outcome is DecisionOutcome.UNAVAILABLE
    assert "换一段证据不会好转" in record.reason
    assert stub.system_one_calls == 0, "量不出来的窗口不该拿去跑推理"


def test_the_internal_question_shape_is_what_the_library_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_internal_question`` 的重命名必须与库一致，否则量的是**别的一道题**的头部。

    替身照抄了库的键读法（``q["t"]`` / ``q["ins"]`` / ``q.get("crit")``），所以键名
    漂了这里会以 ``KeyError`` 的形式红，而不是给一个偏小的 room —— 后者会让
    「装得下」的结论对真正要问的那道题不成立，而截断依然静默。
    """
    stub = _install_laya(monkeypatch)
    provider = LayaDecisionProvider(str(_checkpoint(tmp_path / "ckpt")))
    provider.decide(_case(), mode=DecisionMode.SHADOW)

    assert len(stub.rendered) == 2, "每一道题都要单独量一次"
    assert stub.probes == ["x", "x"], "量头部用的是一个短探针，不是把输入真的跑一遍"
    by_kind = {internal["t"]: internal for internal in stub.rendered}
    assert set(by_kind) == {"choice", "noul"}
    # 是否题**不带** criteria，而选择题带 —— 这不是风格：一个「声明了却没人填」的
    # 字段比缺席更坏（见 ``DecisionSpec.as_questions``），而两个后端都把缺席读成
    # 「没有描述」并回落到各自那句测过的默认措辞。
    assert set(by_kind["choice"]) == {"t", "ins", "crit"}
    assert set(by_kind["noul"]) == {"t", "ins"}


# ---------------------------------------------------------------------------
# Laya：绑定（读装的库的源码，不 import 它）
# ---------------------------------------------------------------------------
def _package_dir(name: str) -> Path | None:
    """包在磁盘上的目录；没装给 ``None``。**不 import 它。**"""
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(list(spec.submodule_search_locations)[0])


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _top_level(module: ast.Module) -> dict[str, ast.AST]:
    """模块**顶层**的名字 → 定义它的节点。函数体里的同名东西一概不算。"""
    found: dict[str, ast.AST] = {}
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found[node.target.id] = node
    return found


def _imported_names(module: ast.Module) -> dict[str, tuple[str, str]]:
    """``from <module> import <name> as <local>`` → ``{local: (module, name)}``。"""
    found: dict[str, tuple[str, str]] = {}
    for node in ast.walk(module):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                found[alias.asname or alias.name] = (node.module, alias.name)
    return found


def _method(node: ast.ClassDef, name: str) -> ast.FunctionDef | None:
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == name:
            return child
    return None


def _positional(fn: ast.FunctionDef) -> list[str]:
    """位置参数与位置-或-关键字参数的名字，**不含** keyword-only 与 ``self``。"""
    return [arg.arg for arg in (*fn.args.posonlyargs, *fn.args.args)]


def _arg_defaults(fn: ast.FunctionDef) -> dict[str, object]:
    """``fn`` 里**带缺省值**的那几个参数 → 那个缺省值（取不出字面量就跳过）。"""
    args = [*fn.args.posonlyargs, *fn.args.args]
    defaults = fn.args.defaults
    if not defaults:
        return {}
    found: dict[str, object] = {}
    for arg, default in zip(args[len(args) - len(defaults) :], defaults):
        try:
            found[arg.arg] = ast.literal_eval(default)
        except ValueError:
            continue
    return found


def _cfg_defaults(fn: ast.FunctionDef) -> dict[str, object]:
    """``fn`` 里所有 ``self.cfg.get("name", <字面量>)`` 的名字 → 那个字面量。"""
    found: dict[str, object] = {}
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "cfg"
            and len(node.args) == 2
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found[node.args[0].value] = ast.literal_eval(node.args[1])
    return found


def test_laya_binds_only_to_names_the_installed_library_has() -> None:
    """我们绑定的那些库名，在**装的** 0.3.4 上确实存在，且形状没变。

    这一条存在的理由：``adapters/laya.py`` 绑得比一般的 adapter 深 —— 它读
    ``DEFAULT_MODELS`` 的键、按 ``_to_internal`` 的键名造题面、拿 ``cfg`` 里两个默认值
    与自己的常量对齐。每一处漂移都是**静默**的：``DEFAULT_MODELS`` 少了 ``english``
    会让每一次判定都变成弃权，而报告上看起来像「模型不肯答」，真相却是我们绑错了名字。

    它读的是**源码**（AST），不是 import —— ``import laya`` 要 3.63 秒并把 ``torch``
    拉进来，而这条判据一秒都不该付那个代价。
    """
    root = _package_dir("laya")
    if root is None:
        pytest.skip("laya 未安装：这一条量的是「装的库上有没有我们绑定的名字」")

    exported = _imported_names(_parse(root / "__init__.py"))
    assert {"DEFAULT_MODELS", "Router", "detect_language"} <= set(exported)
    assert exported["detect_language"] == ("lang", "analyse"), (
        "``laya.detect_language`` 是 ``lang.analyse`` 的别名。它改名之后 "
        "``from laya import detect_language`` 会 ImportError，而那条调用点在 "
        "``decide`` 的 ``try`` 之外 —— 它会以一次崩溃而不是一条记录的形式出现"
    )

    router = _top_level(_parse(root / "router.py"))
    router_cls = router.get("Router")
    assert isinstance(router_cls, ast.ClassDef)
    for name in ("route", "load"):
        assert _method(router_cls, name) is not None, f"Router 不再有 {name}"

    models = router.get("DEFAULT_MODELS")
    assert isinstance(models, ast.Assign) and isinstance(models.value, ast.Dict)
    keys = {key.value for key in models.value.keys if isinstance(key, ast.Constant)}
    assert _LOCAL_MODEL_KEY in keys, (
        f"``DEFAULT_MODELS`` 里没有 {_LOCAL_MODEL_KEY!r} —— ``decide`` 的本地分支"
        "（``chosen == _LOCAL_MODEL_KEY``）永远不会成立，于是每一次判定都变成弃权"
    )
    assert len(keys) >= 2, (
        "只有一个 key 时「路由到别处」那条分支是死代码 —— ABSTAIN 的判据随之失去意义，"
        "而它正是本 adapter 最要紧的那条安全属性"
    )

    router_init = _method(router_cls, "__init__")
    assert router_init is not None
    assert _arg_defaults(router_init).get("default") == _LOCAL_MODEL_KEY, (
        f"``Router`` 的缺省 key 与 ``_LOCAL_MODEL_KEY``（现在是 "
        f"{_LOCAL_MODEL_KEY!r}）不再一致。那个常量说的是「本 adapter 认的本地"
        "checkpoint 是哪一个」，而它的依据就是**库自己**把英文路由到的那一个。两者"
        "分开之后，英文输入会走进弃权分支 —— 而报告上看起来像「模型不肯答」"
    )

    build = _top_level(_parse(root / "common.py")).get("build_sequence")
    assert isinstance(build, ast.FunctionDef)
    assert _positional(build)[:5] == ["tok", "state", "q", "max_len", "head_max_len"], (
        "``build_sequence`` 的前五个参数不再是位置参数 —— ``_room_left`` 按位置调它"
    )

    agent = _top_level(_parse(root / "agent.py")).get("Agent")
    assert isinstance(agent, ast.ClassDef)
    system_one = _method(agent, "system_one")
    assert system_one is not None

    defaults = _cfg_defaults(system_one)
    assert defaults.get("max_len") == laya_module._DEFAULT_MAX_LEN, (
        "``system_one`` 里 max_len 的兜底值与 ``_DEFAULT_MAX_LEN`` 不再一致 —— "
        "那个常量的全部意义就是「与库同值」"
    )
    assert defaults.get("head_max_len") == laya_module._DEFAULT_HEAD_MAX_LEN

    internal = _method(agent, "_to_internal")
    assert internal is not None, "``_internal_question`` 照抄的就是它"
    keys = set()
    for node in ast.walk(internal):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            keys |= {key.value for key in node.value.keys if isinstance(key, ast.Constant)}
    assert keys == {"t", "ins", "crit"}, (
        f"``_to_internal`` 返回的键变成了 {sorted(map(str, keys))} —— "
        "``_internal_question`` 与测试替身都照抄了这一组"
    )


# ---------------------------------------------------------------------------
# Jev：替身
# ---------------------------------------------------------------------------
class _Reply:
    """一条 pydantic 回执的最小替身：只需要 ``model_dump``。"""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def model_dump(self) -> dict[str, object]:
        return dict(self._payload)


class _Usage:
    def __init__(self, *, input_tokens: int | None, output_tokens: int | None) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Response:
    def __init__(
        self, answers: dict[str, Any], *, model: str = "jev-latest", usage: Any = None
    ) -> None:
        self.answers = answers
        self.model = model
        self.usage = usage


class _JevStub:
    """``typesafe_sdk`` 的替身，**含它的环境回退**。

    回退那一处是刻意照抄的：``resolve_and_validate_api_key`` 只在 ``api_key is None``
    时才去读 ``TYPESAFE_API_KEY``。替身照着做，于是「我们不接受环境回退」这句话有了
    执行器 —— 少了这一处，那条用例在一个把空钥匙递给 SDK 的实现上照样绿。
    """

    def __init__(self, *, response: Any = None, call_exc: Exception | None = None) -> None:
        self.response = response
        self.call_exc = call_exc
        self.clients: list[dict[str, Any]] = []
        self.calls: list[tuple[Any, Any]] = []


def _install_jev(monkeypatch: pytest.MonkeyPatch, **knobs: Any) -> _JevStub:
    stub = _JevStub(**knobs)

    class RetryPolicy:
        def __init__(
            self,
            max_retries: int = 2,
            backoff_initial: float = 0.5,
            backoff_max: float = 5.0,
            backoff_jitter: float = 0.25,
        ) -> None:
            self.max_retries = max_retries
            self.backoff_initial = backoff_initial
            self.backoff_max = backoff_max
            self.backoff_jitter = backoff_jitter

    class TypeSafeClient:
        def __init__(
            self,
            *,
            api_key: str | None = None,
            model: str | None = None,
            retry: Any = None,
            timeout: Any = None,
            headers: Any = None,
            transport: Any = None,
            http_client: Any = None,
            base_url: str | None = None,
        ) -> None:
            # 见 _JevStub 的 docstring：这一段是 SDK 的 ``_resolve_string`` 回退的照抄。
            resolved = (
                api_key
                if api_key is not None
                else (os.environ.get("TYPESAFE_API_KEY") or "").strip()
            )
            stub.clients.append(
                {
                    "api_key": resolved,
                    "model": model,
                    "retry": retry,
                    "timeout": timeout,
                    "base_url": base_url,
                }
            )
            if not resolved:
                raise RuntimeError(
                    "No API key was provided. Pass api_key or set the "
                    "TYPESAFE_API_KEY environment variable."
                )
            self._key = resolved

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def system_one(self, state: Any, questions: Any) -> Any:
            stub.calls.append((state, questions))
            if stub.call_exc is not None:
                raise stub.call_exc
            return stub.response

    package = _module("typesafe_sdk")
    package.RetryPolicy = RetryPolicy  # type: ignore[attr-defined]
    package.TypeSafeClient = TypeSafeClient  # type: ignore[attr-defined]

    constants = _module("typesafe_sdk.constants", package)
    constants.DEFAULT_BASE_URL = "https://api.example.invalid"  # type: ignore[attr-defined]
    constants.DEFAULT_MODEL = "jev-latest"  # type: ignore[attr-defined]
    package.constants = constants  # type: ignore[attr-defined]

    for module in (package, constants):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    return stub


def _real_answer_models() -> tuple[Any, Any]:
    """真的 ``ChoiceAnswer`` / ``NoulAnswer``（pydantic 响应模型）。

    用它而不是自己写的 dict，是因为 ``.model_dump()`` 那一层要验的正是**对方的**
    数据契约：``NoulAnswer`` 的线上形状里**没有** ``confidence``，而 ``ChoiceAnswer``
    的四个字段都是必填。这一条只有真模型能说清楚。

    **必须在 :func:`_install_jev` 之前调** —— 之后再调，``importorskip`` 会拿到替身。
    """
    typesafe = pytest.importorskip("typesafe_sdk", reason="装上 SDK 才能用真的响应模型")
    return typesafe.ChoiceAnswer, typesafe.NoulAnswer


# ---------------------------------------------------------------------------
# Jev：能力与凭据
# ---------------------------------------------------------------------------
def test_without_a_key_it_is_not_ready_and_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_jev(monkeypatch)
    provider = JevDecisionProvider(None)
    assert provider.configured is True, "前提：SDK 在"
    assert not provider.ready
    assert "TYPESAFE_API_KEY" in provider.blocker
    assert _A_KEY not in provider.blocker


def test_a_missing_sdk_and_a_missing_key_are_different_blockers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「去装依赖」与「去拿凭据」是两个人的活，合成一句就分不清找谁。"""
    _install_jev(monkeypatch)
    assert "decisions-jev" not in JevDecisionProvider(None).blocker

    monkeypatch.setitem(sys.modules, "typesafe_sdk", None)
    provider = JevDecisionProvider(_A_KEY)
    assert provider.configured is False
    assert "decisions-jev" in provider.blocker
    assert "TYPESAFE_API_KEY" not in provider.blocker


def test_the_environment_fallback_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境里**有** ``TYPESAFE_API_KEY``，而这个 provider 没拿到钥匙 ⇒ 不构造 client。

    让 SDK 读到那份凭据，就等于一次调用用到了一份 ``marketops/config.py`` 看不见的
    钥匙：它不在 ``as_public_dict()`` 的视野里，也不受「凭据只在 vault 里」这条规则的
    约束。替身照抄了 SDK 的回退，所以这条判据真的会红 —— 一个把空钥匙递给 SDK 的
    实现会在这里拿到一条 ``ANSWERED``。
    """
    stub = _install_jev(monkeypatch, response=_Response({}))
    monkeypatch.setenv("TYPESAFE_API_KEY", _A_KEY)

    record = JevDecisionProvider(None).decide(_case(), mode=DecisionMode.SHADOW)

    assert record.outcome is DecisionOutcome.UNAVAILABLE
    assert stub.clients == [], "没有钥匙时根本不该构造 client"
    assert stub.calls == []
    assert _A_KEY not in repr(record)


def test_an_empty_key_never_becomes_none_on_its_way_to_the_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**第二道防线**：即使 ``ready`` 的钥匙检查被去掉，环境回退仍然不成立。

    这一条必须人为绕过 ``ready`` —— 两道防线不共享失效点，第一道在的时候第二道
    永远走不到。而「走不到」正是它需要一条判据的理由：删掉它不会让任何测试变红。

    这里把 ``ready`` 强行翻真，模拟的正是它要防的那次改动（``ready`` 里的钥匙检查
    没了）。替身照抄了 SDK 的回退（只在 ``api_key is None`` 时读环境），所以
    「传下去的是空串还是 ``None``」在这里是可分辨的 —— 这正是这条判据的全部内容。
    """
    stub = _install_jev(monkeypatch, response=_Response({}))
    monkeypatch.setenv("TYPESAFE_API_KEY", _A_KEY)

    provider = JevDecisionProvider(None)
    monkeypatch.setattr(type(provider), "ready", property(lambda self: True))
    record = provider.decide(_case(), mode=DecisionMode.SHADOW)

    assert stub.clients, "前提：这一次真的走到了构造 client"
    assert stub.clients[0]["api_key"] == "", (
        "空钥匙被当成 None 传下去了 —— SDK 会去读 TYPESAFE_API_KEY，而那份凭据"
        "不在 Settings 的视野里"
    )
    assert stub.calls == [], "替身照抄了回退：传 None 它会真的把请求发出去"
    assert record.outcome is DecisionOutcome.UNAVAILABLE
    assert _A_KEY not in repr(record)


def test_the_key_never_reaches_the_record_even_when_the_backend_echoes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """后端抛出的异常文本是**别人写的**，所以出栈的每一段文字都要过一遍抹除。

    ``Config`` 的 ``repr=False`` 只是不主动打印它；一条经过三个中间层的异常消息是否
    带上请求头，不由我们决定。所以这里让后端**故意**把钥匙写进异常里。
    """
    stub = _install_jev(
        monkeypatch, call_exc=RuntimeError(f"401 Unauthorized for api_key={_A_KEY}")
    )
    record = JevDecisionProvider(_A_KEY).decide(_case(), mode=DecisionMode.SHADOW)

    assert stub.clients[0]["api_key"] == _A_KEY, "前提：钥匙确实被传进去了"
    assert record.outcome is DecisionOutcome.UNAVAILABLE
    assert REDACTED in record.reason
    assert _A_KEY not in repr(record), "钥匙出现在了记录的某个字段里"


def test_a_failed_call_is_a_record_and_names_the_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_jev(monkeypatch, call_exc=TimeoutError("read timeout"))
    record = JevDecisionProvider(_A_KEY).decide(_case(), mode=DecisionMode.SHADOW)

    assert record.outcome is DecisionOutcome.UNAVAILABLE
    assert "TimeoutError" in record.reason and "read timeout" in record.reason
    assert record.answers == ()
    assert record.usage.billed is False, "没发出去的那一次不该记成已计费"
    assert _A_KEY not in repr(record)


def test_the_sdk_is_not_allowed_to_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """一次 ``decide`` 只允许发一个计费请求。

    SDK 的缺省是 ``max_retries=2``，也就是一次 ``decide`` 最多发三个请求 —— 而预算
    账本扣的是**一次**（``BudgetLine.INFERENCE``），重试在 SDK 内部发生，账本看不见。
    想重试就让调用方再发起一次判定，那一次会自己走一遍预留。
    """
    stub = _install_jev(monkeypatch, response=_Response({}))
    JevDecisionProvider(_A_KEY).decide(_case(), mode=DecisionMode.SHADOW)

    built = stub.clients[0]
    assert built["retry"].max_retries == 0, "SDK 缺省会重试两次，而账本只扣一次"
    assert built["base_url"] is not None and built["model"] is not None, (
        "base_url / model 要显式传入 —— 留 None 会让 SDK 去读环境，配置就有一半不在"
        "我们手里"
    )


# ---------------------------------------------------------------------------
# Jev：一次成功的判定
# ---------------------------------------------------------------------------
def test_a_successful_call_answers_bills_and_carries_the_replys_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回执走**真的** pydantic 响应模型过一次 ``.model_dump()``。

    这一步是刻意的：``answers_from_payload`` 认的是线上形状，而 ``NoulAnswer`` 的线上
    形状里**没有** ``confidence`` —— 所以这一次的 ``YesNoAnswer.confidence`` 必须是
    ``None``（「后端没报」），而不是我们补的 0 或 1。
    """
    real_choice, real_noul = _real_answer_models()
    stub = _install_jev(
        monkeypatch,
        response=_Response(
            {
                "kind": real_choice(
                    type="choice",
                    choice="venue",
                    confidence=0.9,
                    probabilities={"venue": 0.9, "blog": 0.1},
                ),
                "official": real_noul(type="noul", noul=0.81),
            },
            model="jev-2026-09",
            usage=_Usage(input_tokens=1200, output_tokens=8),
        ),
    )
    record = JevDecisionProvider(_A_KEY).decide(_case(), mode=DecisionMode.SHADOW)

    assert record.outcome is DecisionOutcome.ANSWERED
    assert record.provider == "jev"
    assert record.model == "jev-2026-09", "回执自称的那一个"
    assert record.checkpoint == "jev-latest", "请求里钉住的那一个（SDK 缺省）"
    assert record.usage.billed is True, "托管 API：这次调用产生了外部计量成本"
    assert record.usage.input_tokens == 1200 and record.usage.output_tokens == 8
    assert record.preprocessing.language is None, (
        "托管 API 不做我们能看见的语言检测 —— None 是「这一层没有观察到」"
    )
    assert "托管 API" in record.preprocessing.route
    assert _A_KEY not in repr(record)

    kind, official = record.answers
    assert isinstance(kind, ChoiceAnswer) and kind.choice == "venue"
    assert kind.confidence == 0.9
    assert isinstance(official, YesNoAnswer)
    assert official.probability == 0.81
    assert official.confidence is None, (
        "NoulAnswer 的线上形状里没有 confidence —— None 是「后端没报」"
    )

    # 送出去的题面就是端口那一份，一字不差 —— 两个后端读同一份翻译。
    assert stub.calls == [(_case().state, _spec().as_questions())]


def test_an_empty_reply_set_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """``SystemOneResponse.answers`` 的缺省是空 dict —— 它必须落成一条说明白的记录。"""
    _install_jev(monkeypatch, response=_Response({}, usage=None))
    record = JevDecisionProvider(_A_KEY).decide(_case(), mode=DecisionMode.SHADOW)

    assert record.outcome is DecisionOutcome.UNAVAILABLE
    assert "kind" in record.reason


def test_a_jev_reply_that_contradicts_the_spec_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_choice, real_noul = _real_answer_models()
    _install_jev(
        monkeypatch,
        response=_Response(
            {
                "kind": real_choice(
                    type="choice",
                    choice="maybe",
                    confidence=0.9,
                    probabilities={"maybe": 1.0},
                ),
                "official": real_noul(type="noul", noul=0.5),
            }
        ),
    )
    record = JevDecisionProvider(_A_KEY).decide(_case(), mode=DecisionMode.SHADOW)

    assert record.outcome is DecisionOutcome.UNAVAILABLE
    assert "回执不可用" in record.reason
    assert _A_KEY not in repr(record)


def test_an_answer_object_that_cannot_be_dumped_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SDK 换了响应模型、``model_dump`` 不在了 —— 那要落成一条记录，不是 AttributeError。"""

    class Exploding:
        def model_dump(self) -> dict[str, object]:
            raise ValueError("no field 'confidence' on this model")

    _install_jev(
        monkeypatch,
        response=_Response({"kind": Exploding(), "official": _Reply(_noul_reply())}),
    )
    record = JevDecisionProvider(_A_KEY).decide(_case(), mode=DecisionMode.SHADOW)

    assert record.outcome is DecisionOutcome.UNAVAILABLE
    assert "ValueError" in record.reason and "confidence" in record.reason
    assert _A_KEY not in repr(record)


def test_the_service_defaults_are_still_what_our_constants_say() -> None:
    """``blocker`` 与 ``checkpoint`` 里写死了 SDK 的两个缺省名与变量名。

    这一条量的是**对方**：``DEFAULT_MODEL`` 与 ``API_KEY_ENV`` 改了，我们那两句就是
    错话（一句会把「去拿哪把钥匙」指错，一句会把 checkpoint 记成一个不存在的名字）。
    """
    typesafe = pytest.importorskip("typesafe_sdk")
    from typesafe_sdk.constants import API_KEY_ENV, DEFAULT_BASE_URL, DEFAULT_MODEL

    assert API_KEY_ENV == "TYPESAFE_API_KEY"
    assert DEFAULT_MODEL == "jev-latest"
    assert DEFAULT_BASE_URL
    assert typesafe.RetryPolicy().max_retries != 0, (
        "SDK 的缺省不再重试 —— RetryPolicy(max_retries=0) 就不再是「一次 decide 一个"
        "计费请求」的执行器了，而它在 adapters/jev.py 里是这么被引用的"
    )

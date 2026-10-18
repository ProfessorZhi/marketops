"""Laya：**本机进程里**跑的决策后端（本地 checkpoint，无网络）。

``ports.decisions.DecisionProvider`` 的两个实现之一。它碰的是磁盘（权重）与一个进程
（``torch``），所以按 ``adapters/__init__.py`` 那条判据它属于本层。

## 四件必须先说清楚的事

### 1. 能力报告路径**绝不能** import 它

实测 ``import laya`` 要 3.63 秒，并且把 ``torch`` 拉进 ``sys.modules``。而
``get_capabilities`` 是一条**每条工具调用都可能读**的状态面 —— 让它付这个代价，
等于给一个只想知道「决策后端接上了没」的调用加上一次完整的 torch 导入。所以
:attr:`LayaDecisionProvider.configured` 用的是 ``importlib.util.find_spec``：
它只查路径，不执行模块。这一条有执行器
（``test_laya_capability_report_does_not_load_torch``），因为**把 import 挪到模块层
是一个看起来完全无害的重构**。

### 2. 英文 checkpoint 对非英文不是「退化」，是**崩塌**，而且它同时报高置信

laya 自己的 ``Router`` 文档记着这件事：MASSIVE intent 上印地语 0.100 / 韩语 0.103
（随机基线 0.050），同时 expected calibration error 是 0.855 —— 也就是说它**答错
并且很自信**。一次高置信的错答会一路走进证据，而报告上它与一次正确的判定长得一模
一样。所以本 adapter 把「路由选中的不是我本地的那个 checkpoint」当作**弃权**处理，
并把 ``Router`` 自己写的那句理由逐字记进 ``preprocessing.route``。判断权在库里，
我们只执行它的结论。

### 3. 权重**必须**在本地，而且必须是完整的四件

laya 有四条会去 HuggingFace 取东西的路，各自静默：``Agent.__init__`` 里
``os.path.exists(model_dir)`` 为假时走 ``snapshot_download``、``tokenizer/`` 不存在时
拿 ``cfg["encoder"]`` 去 ``from_pretrained``、``encoder/`` 不存在时同样、
``build_model`` 里的 ``cfg["encoder"]``。任何一条被走上，都是**几百 MB 级的隐式下载**，
落在 ``HF_HOME``（默认在 HOME 下），而宿主策略明确禁止在 HOME 下建大缓存。

所以 :data:`_REQUIRED_CHECKPOINT_ENTRIES` 把那四样钉成 ``ready`` 的前置条件 ——
四条下载路径于是**结构上不可达**，不是一个我可能忘记的检查。这也是 ``configured``
与 ``ready`` 分开的第二个理由（第一个在 ``ports.decisions.DecisionProvider`` 上）。

### 4. 它**会写**权重目录

``Agent.__init__`` 会调 ``laya.agent._fix_tokenizer_config``，那个函数在
``tokenizer/tokenizer_config.json`` 需要修补时**写回原文件**（旧版 transformers 的
``tokenizer_class``/``extra_special_tokens`` 形态）。所以权重目录必须可写，
且按第 3 条它不在仓内。这条不写进 ``ready``：修补只在特定的文件内容下发生，
把一个「可能不需要」的条件放进 ``ready`` 会让一个能跑的部署被报成不可用。
它在这里写明，失败时会以 ``UNAVAILABLE`` + 真实异常文本露面。

## 输入过长时弃权，绝不裁剪

``laya.common.build_sequence`` 的最后一步是 ``st[:room]``：**静默**丢掉 state 的尾部。
§11 禁止静默截断，而这里丢掉的恰好是证据文本的后半段 —— 判定会基于一段被截短的
证据，记录上却只有原文。所以 :func:`_room_left` 用**库自己的那个函数**量出这一题
留给 state 的确切 token 数（不是估算，也不是把它的算术抄一遍），state 超出就弃权。

这条也解释了为什么 checkpoint 的路径进 ``checkpoint`` 字段：它是这条记录里唯一
能回答「哪一批权重给出了这个答案」的东西。
"""

from __future__ import annotations

import importlib.util
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..domain.enums import DecisionMode, DecisionOutcome
from ..domain.errors import ConfigError
from ..domain.util import utc_now
from ..ports.decisions import (
    DecisionAnswer,
    DecisionCase,
    DecisionPreprocessing,
    DecisionProvider,
    DecisionRecord,
    DecisionUsage,
    answers_from_payload,
    record_id,
)

#: 本 adapter 只为**一个** checkpoint 的部署接线。
#:
#: 不是疏忽：路由选中另一个 key 时唯一诚实的处置就是弃权（见模块 docstring §2），
#: 所以「本机装了哪个 checkpoint」才是这个 adapter 的参数，而不是「允许哪几个」。
#: 多语言的权重需要它自己的 key 与一条不同的闸门，等那个部署真的出现再做 ——
#: 与 ``ports/__init__.py`` 立的「等第二个实现出现再提取」是同一条纪律。
_LOCAL_MODEL_KEY = "english"

#: 一个**完整**的本地 checkpoint 必须有的四样东西（见模块 docstring §3）。
#: 相对路径，相对 checkpoint 目录。
_REQUIRED_CHECKPOINT_ENTRIES: tuple[str, ...] = (
    "rl_agent_config.json",  # Agent.__init__ 硬要求；缺了直接 FileNotFoundError
    "model.safetensors",  # 权重本体
    "tokenizer",  # 缺了 → AutoTokenizer.from_pretrained(cfg["encoder"]) → 下载
    "encoder",  # 缺了 → build_model 里 AutoModel.from_pretrained(cfg["encoder"]) → 下载
)

#: 没有本地权重的那几个 key 指向的目录名。它**永远不存在**，于是任何一条漏过闸门的
#: ``Router.load`` 会当场 ``FileNotFoundError``，而不是安静地下载 421M 参数。
#: 这是第二道防线：闸门（``decide`` 里那个 if）给出弃权记录，这一条让**绕过闸门的
#: 编程错误**以异常的形式出现，而不是以一张账单的形式。
_ABSENT_DIR = "__not_installed__"

#: 量 head 长度用的探针。一个字母，几乎必然是一个 token；即使不是，
#: :func:`_room_left` 用的是**实测的**探针长度，不是假设的 1。
_PROBE_STATE = "x"

#: ``agent.cfg`` 缺字段时的兜底值，与 ``laya.agent.Agent.system_one`` 里的默认值
#: 一致（``self.cfg.get("max_len", 512)`` / ``head_max_len`` 192）。写在这里而不是
#: 各写一遍，是因为两处不一致会让「装不装得下」的答案取决于谁先被读到。
_DEFAULT_MAX_LEN = 512
_DEFAULT_HEAD_MAX_LEN = 192


def _installed(name: str) -> bool:
    """``name`` 可不可 import —— **不执行它**。

    用 ``find_spec`` 而不是 ``try: import``，理由见模块 docstring §1。对 ``laya``
    来说那是一次 3.63 秒 + ``torch`` 常驻的代价。
    """
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # 父包坏了 / name 不是合法模块名
        return False


class LayaDecisionProvider(DecisionProvider):
    """本地 checkpoint 上的有界语义判断。

    Args:
        checkpoint_dir: 权重目录的**绝对路径**。``None`` 表示没配 ——
            ``ready`` 为假，``decide`` 给出一条 ``UNAVAILABLE`` 记录而不是崩溃。
            要求绝对路径是有意的：相对路径在 ``Agent`` 眼里不是本地目录，
            于是它会走 ``snapshot_download``（模块 docstring §3）。
    """

    name = "laya"

    def __init__(self, checkpoint_dir: str | None = None) -> None:
        self._checkpoint_dir = checkpoint_dir or ""
        # 整个进程一个 Router：它按 LRU 缓存已加载的 checkpoint，而同一个 421M 的
        # 模型装两遍不是慢，是 OOM（这台机器上还有别的并发工作）。
        self._router: Any = None
        # 一次只跑一次判定。理由同上，外加上 GPU 只有一个。
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ 能力
    @property
    def configured(self) -> bool:
        """依赖在不在。**不问权重**，也不 import 它。"""
        return _installed("laya")

    @property
    def missing_entries(self) -> tuple[str, ...]:
        """checkpoint 目录里缺的那几样（空元组 = 四样都在）。

        单独暴露而不是只进 ``blocker`` 的字符串：``blocker`` 是给人读的一句话，
        而这条判据（``ready``）需要的是**可断言的结构**。测试与将来的诊断都靠它。
        """
        if not self._checkpoint_dir:
            return ("<未配置 checkpoint 目录>",)
        root = Path(self._checkpoint_dir)
        if not root.is_absolute():
            # 相对路径会被 Agent 当成 repo id 去下载。这一条不是风格要求。
            return (f"<{self._checkpoint_dir!r} 不是绝对路径>",)
        if not root.is_dir():
            return (f"<{self._checkpoint_dir!r} 不是一个目录>",)
        return tuple(
            entry for entry in _REQUIRED_CHECKPOINT_ENTRIES if not (root / entry).exists()
        )

    @property
    def ready(self) -> bool:
        """**能不能真的产出一条记录。** 调用方按这一个决定接不接。"""
        return self.configured and not self.missing_entries

    @property
    def blocker(self) -> str:
        """``ready`` 为假时的理由。**不含任何凭据**（本后端没有凭据）。"""
        if not self.configured:
            return "laya 不可 import（本机未安装）：pip install -e '.[decisions-laya]'"
        missing = self.missing_entries
        if not missing:
            return ""
        return (
            f"checkpoint 目录 {self._checkpoint_dir!r} 缺 {list(missing)}。"
            "四样都要在本地：缺任何一样 laya 都会去 HuggingFace 取，"
            "而本 adapter 不做隐式下载。"
        )

    # ------------------------------------------------------------------ 判定
    def decide(self, case: DecisionCase, *, mode: DecisionMode) -> DecisionRecord:
        """见 ``ports.decisions.DecisionProvider.decide``。"""
        started = time.monotonic()
        decided_at = utc_now().isoformat()
        questions = case.spec.as_questions()

        if not self.ready:
            return self._record(
                case,
                mode=mode,
                decided_at=decided_at,
                elapsed=started,
                outcome=DecisionOutcome.UNAVAILABLE,
                reason=self.blocker,
                language=None,
                route="",
                input_tokens=None,
            )

        with self._lock:
            try:
                router = self._ensure_router()
            except Exception as exc:  # noqa: BLE001 - 装载失败是部署问题，见下
                return self._record(
                    case, mode=mode, decided_at=decided_at, elapsed=started,
                    outcome=DecisionOutcome.UNAVAILABLE,
                    reason=f"laya 装载失败：{type(exc).__name__}: {exc}",
                    language=None, route="", input_tokens=None,
                )

            # ``route`` 只做判断、不加载任何东西（库自己的 docstring）。
            decision = router.route(case.state, questions)
            chosen = str(decision["model"])
            why = str(decision["reason"])
            route = f"{chosen}: {why}"
            language = self._language(case.state)

            if chosen != _LOCAL_MODEL_KEY:
                # 见模块 docstring §2：非英文走 multilingual，而那个 checkpoint
                # 本机没有 —— 用英文权重去答非英文会得到一个高置信的错答。
                return self._record(
                    case, mode=mode, decided_at=decided_at, elapsed=started,
                    outcome=DecisionOutcome.ABSTAINED,
                    reason=(
                        f"路由选中 {chosen!r}（{why}），而本机只装了 "
                        f"{_LOCAL_MODEL_KEY!r} 的权重。用英文 checkpoint 答非英文不是"
                        "退化而是崩塌，且它同时报高置信 —— 所以这里弃权，"
                        "不给出一条看起来很确定的错答。"
                    ),
                    language=language, route=route, input_tokens=None,
                )

            try:
                agent = router.load(chosen)
            except Exception as exc:  # noqa: BLE001 - 同上，装载失败是部署问题
                return self._record(
                    case, mode=mode, decided_at=decided_at, elapsed=started,
                    outcome=DecisionOutcome.UNAVAILABLE,
                    reason=f"laya 装载 {chosen!r} 失败：{type(exc).__name__}: {exc}",
                    language=language, route=route, input_tokens=None,
                )

            too_long = self._over_length(agent, questions, case.state)
            if too_long is not None:
                # 输入太长是**这一次输入**的问题（换一段证据还能跑）⇒ ABSTAINED；
                # 题面头部装不下是**题面与这批权重**对不上（每一条输入都会失败）
                # ⇒ UNAVAILABLE。两者的处置不同，所以这里不是同一个 outcome。
                outcome, reason = too_long
                return self._record(
                    case, mode=mode, decided_at=decided_at, elapsed=started,
                    outcome=outcome, reason=reason,
                    language=language, route=route, input_tokens=None,
                )

            try:
                payload = agent.system_one(case.state, questions)
            except Exception as exc:  # noqa: BLE001 - 见下
                # 库在这里抛的类型不稳定（ValueError / RuntimeError / OOM 回退失败），
                # 而**不可用是一种可审计的结果，不是一次崩溃**（``DecisionProvider``）。
                # 理由里带类型与原文，所以它不是一条含糊的 gap。
                return self._record(
                    case, mode=mode, decided_at=decided_at, elapsed=started,
                    outcome=DecisionOutcome.UNAVAILABLE,
                    reason=f"laya 推理失败：{type(exc).__name__}: {exc}",
                    language=language, route=route, input_tokens=None,
                )

        try:
            answers = answers_from_payload(
                payload.get("answers") or {}, case.spec, provider=self.name
            )
        except ConfigError as exc:
            # 回执与题面对不上 —— 部署在那一头的题面与我们手里的不是同一份。
            return self._record(
                case, mode=mode, decided_at=decided_at, elapsed=started,
                outcome=DecisionOutcome.UNAVAILABLE,
                reason=f"laya 的回执与题面不符：{exc}",
                language=language, route=route, input_tokens=None,
            )

        usage = payload.get("usage") or {}
        return self._record(
            case, mode=mode, decided_at=decided_at, elapsed=started,
            outcome=DecisionOutcome.ANSWERED, reason="", answers=answers,
            language=language, route=route,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            model=str(payload.get("model") or ""),
            checkpoint=self._checkpoint_dir,
            # 本地进程，没有外部计量成本。§8 的金额字段刻意不存在 ——
            # 缺省是「未知」，不是「0 元」。
            billed=False,
        )

    # ------------------------------------------------------------------ 内部
    def _ensure_router(self) -> Any:
        """建一次，之后一直用同一个（见 ``__init__`` 的注释）。

        没有本地权重的 key 指向一个**不存在**的目录（:data:`_ABSENT_DIR`）。
        不能只靠 ``decide`` 里那个闸门：那是一条我会忘的检查，而这是一条
        **不可达的路**。
        """
        if self._router is not None:
            return self._router

        import laya

        root = Path(self._checkpoint_dir)
        models = {
            key: (
                str(root)
                if key == _LOCAL_MODEL_KEY
                else str(root / _ABSENT_DIR / key)
            )
            for key in laya.DEFAULT_MODELS
        }
        self._router = laya.Router(models=models, max_loaded=1, default=_LOCAL_MODEL_KEY)
        return self._router

    def _language(self, state: str) -> str | None:
        """laya 自己的语言检测。**用的是它，不是另写一个。**

        另写一个检测器就是「两份真实实现」—— 而两份会漂移的那一天，闸门放行的
        与路由选择的就是不同的东西。``analyse`` 是纯 Python（不需要权重），
        但它住在 ``laya`` 里面，所以这个调用点必然在 ``import laya`` 之后。
        """
        from laya import detect_language

        return detect_language(state).get("language")  # type: ignore[return-value]

    def _over_length(
        self, agent: Any, questions: Mapping[str, Mapping[str, object]], state: str
    ) -> tuple[DecisionOutcome, str] | None:
        """装不下就给 ``(outcome, 理由)``，装得下给 ``None``。

        **量的是库自己的算术**，不是把它的算术抄一遍 —— 抄一遍的版本会在库升级时
        与真实行为漂移，而漂移的方向恰好是「我们以为没截断、它截断了」。
        """
        max_len = int(agent.cfg.get("max_len", _DEFAULT_MAX_LEN))
        rooms = {qid: _room_left(agent, question) for qid, question in questions.items()}
        too_big = sorted(qid for qid, room in rooms.items() if room is None)
        if too_big:
            return (
                DecisionOutcome.UNAVAILABLE,
                f"题面与它的选项本身就把这个 checkpoint 的窗口占满了（{too_big}），"
                "留给 state 的余量已经量不出来。这不是输入太长，是题面（服务端持有的"
                "spec）与这批权重的容量对不上 —— 换一段证据不会好转。",
            )
        # 取各题里**最小**的那一个余量：``system_one`` 一题一条序列，而一段 state
        # 只要放不进其中任何一题，那一题就会被库静默裁剪。按最小的接线是失败关闭
        # 的那一侧 —— 代价是可能多弃权一次，收益是不会有一条被裁过的记录溜过去。
        room = min(room for room in rooms.values() if room is not None)
        state_tokens = len(agent.tok(state, add_special_tokens=False)["input_ids"])
        if state_tokens <= room:
            return None
        return (
            DecisionOutcome.ABSTAINED,
            f"输入 {state_tokens} token，而这个 checkpoint（窗口 {max_len}）给 state 留的"
            f"位置最小是 {room} token（各题里最紧的那一道）。本端口不裁剪：被截掉的是"
            "证据文本的后半段，而记录里只有原文 —— 那样得到的判定看起来正常，判的却"
            "是另一段文本。",
        )

    def _record(
        self,
        case: DecisionCase,
        *,
        mode: DecisionMode,
        decided_at: str,
        elapsed: float,
        outcome: DecisionOutcome,
        reason: str,
        language: str | None,
        route: str,
        input_tokens: int | None,
        answers: tuple[DecisionAnswer, ...] = (),
        output_tokens: int | None = None,
        model: str = "",
        checkpoint: str = "",
        billed: bool = False,
    ) -> DecisionRecord:
        """一条回执。三个 outcome 走同一条构造路径 —— 差别只在参数里。

        这不是为了少写代码：``record_id`` 必须对**没做成的那一次**也算得出来，
        否则「同一次判定重试后得到两条 id 不同的记录」这件事在失败路径上失控。
        """
        return DecisionRecord(
            record_id=record_id(case, provider=self.name, decided_at=decided_at),
            case_id=case.case_id,
            spec_id=case.spec.spec_id,
            spec_version=case.spec.version,
            input_sha256=case.input_sha256,
            provider=self.name,
            model=model,
            checkpoint=checkpoint,
            preprocessing=DecisionPreprocessing(
                language=language, route=route, input_chars=len(case.state)
            ),
            outcome=outcome,
            answers=answers,
            reason=reason,
            latency_ms=int((time.monotonic() - elapsed) * 1000),
            usage=DecisionUsage(
                input_tokens=input_tokens, output_tokens=output_tokens, billed=billed
            ),
            decided_at=decided_at,
            mode=mode,
            evidence_refs=case.evidence_refs,
        )


def _internal_question(payload: Mapping[str, object]) -> dict[str, object]:
    """题面的**线上形状** → laya 内部的 ``{"t", "ins", "crit"}``。

    只有这一处重命名，而且它必须与 ``laya.agent.Agent._to_internal`` 一致 ——
    不一致的后果是 :func:`_room_left` 量的是**别的一道题**的头部长度，于是
    「装得下」的结论对真正要问的那道题不成立，而截断依然静默。

    这条重命名的执行器不是自觉：``test_the_room_matches_the_library_sequence_builder``
    拿真实的 ``laya.common.build_sequence`` 跑，键名漂了它就红。
    """
    internal: dict[str, object] = {"t": payload["type"], "ins": payload["instructions"]}
    if "criteria" in payload:
        internal["crit"] = payload["criteria"]
    return internal


def _room_left(agent: Any, payload: Mapping[str, object]) -> int | None:
    """这一题留给 state 的 token 数；量不出来时给 ``None``。

    做法是**用一个短探针把头部量出来**，而不是复述 ``build_sequence`` 的算术：

    ``build_sequence`` 拼出 ``[CLS] 头部 [SEP] 选项… [SEP] state [SEP]``，然后
    ``room = max_len - len(头部那一半) - 1``，再把 state 裁到 ``room``。所以用一个
    几乎不含 token 的探针跑一遍，得到的序列长度就是「头部那一半 + 探针 + 1」，
    于是 ``room = max_len - len(序列) + 探针的 token 数``。

    探针长度是**实测**的：不同 tokenizer 对 ``"x"`` 未必给一个 token，而假设成 1
    会让 room 差几个 token —— 差的那几个正好是「我们以为装得下」的那几个。

    ``None`` 说的是**这个公式在这一题上失效了**，而不是「头部装不下」：当探针
    序列已经顶到 ``max_len``，``build_sequence`` 的最后一行 ``ids[:max_len]``
    会把它裁短，而上面那个减法于是变成 ``max_len - max_len + 探针`` —— 一个**高估**，
    高估的方向恰好是放行一段会被库截掉的 state。所以这里宁可说「量不出来」，由
    :meth:`LayaDecisionProvider._over_length` 归成 ``UNAVAILABLE``（换输入不会好转，
    因为它只取决于题面与这批权重，与 state 无关）。
    """
    from laya.common import build_sequence

    max_len = int(agent.cfg.get("max_len", _DEFAULT_MAX_LEN))
    head_max_len = int(agent.cfg.get("head_max_len", _DEFAULT_HEAD_MAX_LEN))
    probe_tokens = len(agent.tok(_PROBE_STATE, add_special_tokens=False)["input_ids"])
    sequence, _ = build_sequence(
        agent.tok, _PROBE_STATE, _internal_question(payload), max_len, head_max_len
    )
    if len(sequence) >= max_len:
        return None
    return max_len - len(sequence) + probe_tokens


__all__ = ["LayaDecisionProvider"]

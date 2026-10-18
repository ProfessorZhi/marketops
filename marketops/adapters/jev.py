"""Jev：**托管**的决策后端（TypeSafe AI 的 System One，走网络）。

``ports.decisions.DecisionProvider`` 的另一个实现。它碰的是网络与一份凭据，所以按
``adapters/__init__.py`` 那条判据它属于本层。

## 与 ``adapters/laya.py`` 的关系

两者是同一个产品族的托管版与本地版：题面与回执的**线上形状相同**，所以那两个翻译
（``DecisionSpec.as_questions`` / ``answers_from_payload``）住在端口里、只有一份。
本文件因此只管三件本地版没有的事：凭据、网络、账单。

## 凭据

**只由构造函数传进来**，本文件一行 ``os.environ`` 都不读 —— ``marketops/config.py``
是全仓唯一读环境的地方（``Settings``），凭据的所有权在那里。

而且**不接受 SDK 的环境回退**。``typesafe_sdk`` 的 ``_core/config.py`` 里
``resolve_and_validate_api_key`` → ``_resolve_string(api_key, API_KEY_ENV)``，而后者
**只在 ``api_key is None`` 时才去读 ``TYPESAFE_API_KEY``**。让它读到，就等于一次调用
用到了一份 ``config.py`` 看不见的凭据 —— 那份凭据不在 ``as_public_dict()`` 的视野里，
也不受「凭据只在 vault 里」这条规则的约束。

两道防线各自都成立，而且它们不共享同一个失效点：

* :attr:`JevDecisionProvider.ready` 要求**手里有一把非空的钥匙** —— 没有就根本不去
  构造 client（``decide`` 在那之前就返回了）；
* 即使有人把上一条去掉，构造时传进去的是**空串**而不是 ``None``（``__init__`` 那行
  ``(api_key or "").strip()``），而空串不是 ``None``，所以那条回退路仍然不成立。

``base_url`` / ``model`` 同理显式传入：让 SDK 去读环境，配置就有一半不在我们手里。

## 一次判定 = 一次计费请求

``RetryPolicy(max_retries=0)``。SDK 的缺省是 ``max_retries=2``，也就是一次 ``decide``
最多发三个请求 —— 而预算账本扣的是**一次**（``BudgetLine.INFERENCE``），
重试在 SDK 内部发生，账本看不见。§5 要求「预算由工具强制，不依赖 prompt 声明」，
这条在这里是：**想重试，就让调用方再发起一次判定**，那一次会自己走一遍预留。

## 记录里不可能出现那把钥匙

``reason`` 由后端抛出的异常文本构成，而异常文本是别人写的。所以所有出栈的文本都
过一遍 :meth:`JevDecisionProvider._scrub` —— 把**我们手里那把钥匙**逐字替换掉。
这不是「相信 SDK 不会打印它」：``Config`` 的 ``repr=False`` 只是不主动打印，
而一条经过三个中间层的异常消息是否带上请求头，不由我们决定。这条有执行器。
"""

from __future__ import annotations

import importlib.util
import time
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

#: 替换凭据用的字面量。写在这里而不是内联，是因为测试要断言**它**出现、
#: 且钥匙不出现 —— 一个各写一遍的常量会在某一处漂移，而漂移的那一次是静默的。
REDACTED = "[REDACTED]"


def _installed(name: str) -> bool:
    """``name`` 可不可 import，**不执行它**。

    与 ``adapters/laya.py`` 的同名函数是同一个判据的两次出现 —— 但代价不同：
    ``typesafe_sdk`` 只拉 ``pydantic`` 与 ``httpx2``（几毫秒级），而 ``laya`` 会拉
    ``torch``（3.63 秒）。两处都写 ``find_spec`` 是为了**形状一致**：
    ``configured`` 这个属性在任何一个 adapter 上都不该有副作用。
    """
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


class JevDecisionProvider(DecisionProvider):
    """托管 API 上的有界语义判断。

    Args:
        api_key: 凭据本体。``None`` / 空串 ⇒ ``ready`` 为假，``decide`` 给出一条
            ``UNAVAILABLE`` 记录。**本机今天就是这一种**（vault 里没有这一份），
            所以本 adapter 的线上路径尚未被真实调用过 —— 它报的是 ``configured``
            为真、``ready`` 为假，而不是一条假的通过。
        base_url: API 根。``None`` ⇒ 用 SDK 自己的 ``DEFAULT_BASE_URL``，
            **不是**用环境变量里的那一个（见模块 docstring）。
        model: 请求的模型名。``None`` ⇒ 用 SDK 的 ``DEFAULT_MODEL``。
        timeout: 单次 HTTP 操作的秒数。``None`` ⇒ 用 SDK 自己的缺省。
    """

    name = "jev"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._base_url = base_url
        self._model = model
        self._timeout = timeout

    # ------------------------------------------------------------------ 能力
    @property
    def configured(self) -> bool:
        """SDK 在不在。**不问凭据** —— 那由 ``ready`` 回答。"""
        return _installed("typesafe_sdk")

    @property
    def ready(self) -> bool:
        """能不能真的产出一条记录：SDK 在，且手里有一把钥匙。"""
        return self.configured and bool(self._api_key)

    @property
    def blocker(self) -> str:
        """``ready`` 为假时的理由。**不含凭据，也不说它在哪。**"""
        if not self.configured:
            return "typesafe_sdk 不可 import（本机未安装）：pip install -e '.[decisions-jev]'"
        if not self._api_key:
            return (
                "缺 TYPESAFE_API_KEY。§20：缺凭据不得写 PASS，所以这条后端今天"
                "只报 unavailable，不报「跑过了」。"
            )
        return ""

    # ------------------------------------------------------------------ 判定
    def decide(self, case: DecisionCase, *, mode: DecisionMode) -> DecisionRecord:
        """见 ``ports.decisions.DecisionProvider.decide``。"""
        started = time.monotonic()
        decided_at = utc_now().isoformat()

        if not self.ready:
            return self._record(
                case, mode=mode, decided_at=decided_at, elapsed=started,
                outcome=DecisionOutcome.UNAVAILABLE, reason=self.blocker,
            )

        try:
            from typesafe_sdk.constants import DEFAULT_BASE_URL, DEFAULT_MODEL
            from typesafe_sdk import RetryPolicy, TypeSafeClient
        except ImportError as exc:  # pragma: no cover - ready 已经查过一次
            return self._record(
                case, mode=mode, decided_at=decided_at, elapsed=started,
                outcome=DecisionOutcome.UNAVAILABLE,
                reason=self._scrub(f"typesafe_sdk 装载失败：{exc}"),
            )

        requested = self._model or DEFAULT_MODEL
        # 题面的翻译在 try **之外**：它是我们自己的代码，它的失败是一次 bug，
        # 而下面那个 except 收的是网络与后端 —— 两者的处置完全不同。
        questions = case.spec.as_questions()
        try:
            with TypeSafeClient(
                api_key=self._api_key,
                base_url=self._base_url or DEFAULT_BASE_URL,
                model=requested,
                timeout=self._timeout,
                # 见模块 docstring：一次 decide 一次请求。
                retry=RetryPolicy(max_retries=0),
            ) as client:
                response = client.system_one(case.state, questions)
        except Exception as exc:  # noqa: BLE001 - 见下
            # 异常的层次不抓具体类型：``TypeSafeError`` 一族的成员会随 SDK 版本增减，
            # 而**不可用是一种可审计的结果，不是一次崩溃**。类型与原文都进理由，
            # 所以它不是一条含糊的 gap —— 是「谁在什么时候为什么没给出结论」。
            return self._record(
                case, mode=mode, decided_at=decided_at, elapsed=started,
                outcome=DecisionOutcome.UNAVAILABLE,
                reason=self._scrub(f"jev 调用失败：{type(exc).__name__}: {exc}"),
                checkpoint=requested,
            )

        try:
            answers = answers_from_payload(
                _answer_payloads(response), case.spec, provider=self.name
            )
        except ConfigError as exc:
            return self._record(
                case, mode=mode, decided_at=decided_at, elapsed=started,
                outcome=DecisionOutcome.UNAVAILABLE,
                reason=self._scrub(f"jev 的回执不可用：{exc}"),
                checkpoint=requested,
            )

        return self._record(
            case, mode=mode, decided_at=decided_at, elapsed=started,
            outcome=DecisionOutcome.ANSWERED, reason="", answers=answers,
            model=str(getattr(response, "model", "") or ""),
            checkpoint=requested,
            input_tokens=getattr(getattr(response, "usage", None), "input_tokens", None),
            output_tokens=getattr(getattr(response, "usage", None), "output_tokens", None),
            # 托管 API：这次调用产生了外部计量成本。金额**刻意不记** —— §8 要求
            # 未知的外部用量在对账之前保持不确定，真实成本由预算账本结算。
            billed=True,
        )

    # ------------------------------------------------------------------ 内部
    def _scrub(self, text: str) -> str:
        """把**我们手里那把钥匙**从出栈文本里逐字抹掉。

        钥匙为空时不做任何事（没什么可抹的）。见模块 docstring 最后一段：
        这不是「相信 SDK 不会打印它」。
        """
        if not self._api_key:
            return text
        return text.replace(self._api_key, REDACTED)

    def _record(
        self,
        case: DecisionCase,
        *,
        mode: DecisionMode,
        decided_at: str,
        elapsed: float,
        outcome: DecisionOutcome,
        reason: str,
        answers: tuple[DecisionAnswer, ...] = (),
        model: str = "",
        checkpoint: str = "",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        billed: bool = False,
    ) -> DecisionRecord:
        """一条回执。与 ``adapters/laya.py`` 的同名方法形状一致 —— 差异只在字段上。

        与本地版一样，没有答案的那几种 outcome 也走这条路径：``record_id`` 必须对
        没做成的那一次也算得出来。
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
                # 托管 API 不做我们能看见的语言检测 —— 它在那一头。``None`` 是
                # 「这一层没有观察到」，不是「这段文本没有语言」。
                language=None,
                route=(
                    f"{checkpoint or self._model or '<SDK 缺省模型>'}: 部署配置固定的模型，"
                    "托管 API 没有本地路由"
                ),
                input_chars=len(case.state),
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


def _answer_payloads(response: Any) -> dict[str, dict[str, object]]:
    """回执里的回答 → **线上形状**的普通 dict。

    ``answers_from_payload`` 认的是线上形状（两个后端共用的那一份），而 SDK 给的是
    pydantic 模型，所以这里拆一层。用 ``model_dump()`` 而不是逐个读属性：
    **缺哪个属性**随 SDK 版本变，而 dict 里缺一个键会让共用翻译给出它自己那条
    拒绝理由（「没有回答题号 q1」），那是一条比 ``AttributeError`` 有用得多的记录。

    取不出字段时抛 ``ConfigError`` 而不是返回一句话：调用点只有一个 ``except``，
    而「回执不可用」在这一层是一件事，不是两种返回值。
    """
    payloads: dict[str, dict[str, object]] = {}
    for name, answer in (getattr(response, "answers", None) or {}).items():
        try:
            payloads[name] = answer.model_dump()
        except Exception as exc:  # noqa: BLE001 - pydantic 的失败形态不稳定
            raise ConfigError(
                f"回答 {name!r} 取不出字段：{type(exc).__name__}: {exc}"
            ) from exc
    return payloads


__all__ = ["JevDecisionProvider", "REDACTED"]

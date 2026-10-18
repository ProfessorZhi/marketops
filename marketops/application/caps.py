"""上限截断，以及**被截掉的那部分**。

上限本身不是这里决定的 —— 它来自 ``Settings``（一个冻结的配置值）。这个模块只管
两件事：按上限切，以及把切掉的东西**记成数据**。

# 为什么「切掉多少」必须是一个数字

「没找到」与「找到了但被上限砍了」在报告里长得一模一样：两者都表现为候选变少。
于是「这次没发现新东西」可以完全是封顶造成的，而读报告的人没有任何办法把它认出来
—— 除非被丢掉的那部分有一个自己的字段。

这不是假想的失败：``docs/CURRENT_STATE.md`` §12 的地雷 #2 记的就是本仓原来的样子 ——
``pipeline._discover`` 把候选 URL 切到 ``max_candidates_per_account``，
而 ``metrics.candidate_urls`` 记的是**切后**值。切前的数一个字都没留下。

# 三条判据（都不是风格）

* **``None`` = 不封顶，而且负数是错误。** 一个负数不是一个「很小的上限」，
  它是调用方算错了。旧实现用 ``-1`` 表示不封顶 —— 那是一个需要注释才读得懂的
  哨兵值，与「有上限但确实很小」共用同一个类型。本仓的方言是 ``None`` 表示
  「这件事没有入口」（见 ``ports/`` 里那几个 ``None`` 提供方），负数在这里
  **失败关闭**（``ConfigError``），不静默当成不封顶。
* **丢的是尾部，顺序承重。** 顺序由上游的确定性排序定死
  （``application/openweb.py`` 那一族会说明谁先出第二条），所以「被砍掉的是哪几条」
  是可复现的。如果顺序是随机的，这份 ``dropped_items`` 就只是一份噪音。
* **``cap == len(items)`` 不是截断。** 恰好装得下时 ``dropped == 0``、
  ``truncated is False``。把「正好用满」记成截断，会让「达到上限」这个信号
  在边界上恒定地谎报一次。

# 泛化到第二个调用点的那一天就是今天

这个模块原来写着：「截断**候选行**（不是 URL）时，需要的是**同一个**决定类型，
但那时才决定要不要把它泛化 —— 现在泛化，唯一的调用点就得写一个恒等的 ``key=str``，
而多出来的那个参数没有任何东西在考验它。」

现在第二个调用点到了：``application/maintenance.py`` 截断的是 ``DueWork`` 行，
不是字符串，所以它必须给出一个 ``key``。那个参数**现在**有东西在考验它了 ——
这就是本仓「等第二个实现出现再提取端口」那条纪律在**函数签名**上的样子：
不是不等，是等到有东西能证明这个参数是对的的时候再等。

泛化的方式是**加一个可选参数**，不是加一个平行函数。两个平行函数会让
「丢的是尾部、``cap == len(items)`` 不算截断、负数失败关闭」这三条判据各有两份实现，
而它们的下一处分歧没人会发现。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ..domain.errors import ConfigError

__all__ = ["TruncationDecision", "apply_cap"]


@dataclass(frozen=True)
class TruncationDecision:
    """一次上限截断，把丢掉的部分**记下来**。

    ``kept + dropped`` 恒等于输入长度 —— 两个数字各自都能算出来，但两个都留着：
    报告里「候选 12 条」那一行与「另外 8 条被上限砍掉」那一行是两句话，
    合成一个「共 20 条」会把上限造成的损失藏进一个更大的数字里。

    ``dropped_items`` 保留的是**丢掉的那几条本身**（不是摘要）：人的下一条问题
    永远是「砍掉的是哪几条」。它不进报告（报告里只有 ``dropped`` 这个数），
    因为它可能很长，而报告要能被一眼读完。
    """

    kept: int
    dropped: int
    #: 生效的上限。``None`` = **没有上限**（不是「上限是 0 」，也不是一个很大的数）。
    cap: int | None = None
    #: 被丢掉的那几条，按原顺序。``dropped == len(dropped_items)``。
    dropped_items: tuple[str, ...] = ()

    @property
    def truncated(self) -> bool:
        """是否真的丢了东西。恰好装得下时是 ``False``。"""
        return self.dropped > 0

    def as_dict(self) -> dict[str, Any]:
        """写进报告的形状。``truncated`` 也写出来，不留给读者现算。"""
        return {
            "cap": self.cap,
            "kept": self.kept,
            "dropped": self.dropped,
            "truncated": self.truncated,
            "dropped_items": list(self.dropped_items),
        }


def apply_cap(
    items: Sequence[Any],
    cap: int | None,
    *,
    key: Callable[[Any], str] | None = None,
) -> tuple[list[Any], TruncationDecision]:
    """按上限切，并回报**被丢掉的那几条**。

    Args:
        items: 已排好序的候选项。顺序承重 —— 见模块 docstring。
        cap: 上限。``None`` = 不封顶。负数拒绝（不是一个很小的上限，是算错了）。
        key: 把一个条目变成它在 ``dropped_items`` 里的**名字**。缺省只接受
            ``str`` 元素（历史上唯一的调用形状是 URL 列表）；非字符串的条目
            **必须**自带 key —— 见 :func:`_identity_key`。

    Returns:
        ``(保留的列表, 决定)``。返回的列表是**新的**，不是 ``items`` 的视图：
        调用方拿到它之后会往下传、可能被改，而 ``TruncationDecision`` 里的数字
        必须还是切那一刻的数字。

    Raises:
        ConfigError: ``cap`` 是负数，或者缺省 key 遇到了非 ``str`` 的条目。
    """
    namer = key or _identity_key
    if cap is None:
        kept = list(items)
        return kept, TruncationDecision(kept=len(kept), dropped=0, cap=None)
    if cap < 0:
        raise ConfigError(
            f"上限不能是负数（收到 {cap}）。不封顶写成 None —— 一个负数不是一个"
            "「很小的上限」，它是算错了；静默当成不封顶会让一次配置错误变成"
            "「没设上限」，而两者在报告上长得一样。"
        )
    kept = list(items[:cap])
    dropped = list(items[cap:])
    return kept, TruncationDecision(
        kept=len(kept),
        dropped=len(dropped),
        cap=cap,
        dropped_items=tuple(namer(item) for item in dropped),
    )


def _identity_key(item: Any) -> str:
    """缺省的名字就是条目自己 —— 但它只能是 ``str``。

    一个非字符串的条目走到这里，说明调用方忘了给 ``key``。两种收场都不好：
    要么静默 ``repr()``（``dropped_items`` 变成一串 pydantic 的完整打印，长到
    没人会读），要么抛。本仓在这里抛,并且说清该怎么办 —— 一个「读不出来」的
    ``dropped_items`` 比一个空的更糟：它看起来是有内容的。
    """
    if not isinstance(item, str):
        raise ConfigError(
            f"apply_cap 的缺省 key 只接受 str，收到 {type(item).__name__}。"
            "非字符串条目必须自带 key —— ``dropped_items`` 是给人读的"
            "「砍掉的是哪几条」，不是一份对象转储。"
        )
    return item

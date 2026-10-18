"""一次校验失败的**一行**说法（嵌进一条逐行的去向里）。

## 为什么这是一格共用的渲染，而不是两处各写一遍

它有两个调用方，而两处解决的是同一条要求：

* ``application/outreach.py`` ——「一次坏提议不得毁掉整批草稿」；
* ``application/relations.py`` ——「一行坏判定不得毁掉整批判定」。

两处都要把一条 ``pydantic.ValidationError`` 摊成一句话，塞进那一行自己的理由里。
各写一遍的代价不是那三行：先漂的是「哪些格子进这句话」，于是一份回执说
``body: String should have at least 1 character``、另一份说 ``String should have at
least 1 character``，而两份说的是同一件事。

## 它与 ``product_loop.py::_readable`` 是同一件事的**另一种形状**，而两种都要有

那个把整次调用的失败摊成**多行**要点，供一个 ``ToolError`` 的正文读；这个是一行，
要嵌进逐行去向里（一行里再嵌几行，那一行的边界就没了）。所以 ``_readable`` 留在
它自己的模块里：形状不同，读者不同，改一处的理由对另一处不成立。

## 它只做两件事，而两件都写在这里

1. **有路径就给路径，没有就不给。** ``@model_validator(mode="after")`` 报出来的错误
   **没有字段路径** —— pydantic 给的是一个空段。照 ``".".join(loc)`` 拼出来就是
   ``": 这一行没有名字"``：那句话读起来像「有一个名字为空的字段」，而事实是这条判据
   管的是**整行**。
2. **摘掉 pydantic 给自己抛出来的错误套的那半句英文标签**（见 :data:`_LABELS`）。

**它不加任何别的东西** —— 尤其不加输入值：理由会被读、被留档，而输入值里可能是客户
的名字。判据用等号（``tests/test_application_validation.py``），一个顺手把输入值补进
消息里的实现在「包含」上完全看不出来。

## 它不折叠消息里的换行

本仓的判定话是**多行**写的（模型校验器里那些「理由 + 接下来该怎么做」），那是它自己的
形状。折叠会把一段有意排过版的话压成一行，而这一层没有判断哪一段该被压平的依据 ——
它只负责把几格接成一句，不负责改写任何一格的内容。

## :func:`as_text` 为什么也在这里

它回答的是同一个问题（「这一行交上来的原文是什么」），而且它与
:func:`one_line_reason` 共用同一批读者：一条逐行去向的 ``reason`` 与它旁边那两格
「你交的是这个」。三个调用方（``application/relations.py`` /
``application/accounts.py`` 两处生产者，加上写理由的这一处）里任意两处各写一遍，
先漂的是「什么算原文」——而两份回执对同一行给出两个说法时，交上来二十行的人就没法
拿它回去核对。

**它不是另外两个同名函数。** 仓里还有 ``motherpool.py::_as_text``（把上游的列值
**规范化**成文本：``None`` → ``""``、布尔 → ``true``）与 ``shadow.py::_as_text``
（把配置里的一格**校验**成非空字符串，不合格就抛）。三个名字像，做的是三件事，
所以这个名字不叫 ``as_text`` 之外的东西，也不去合并它们。
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

__all__ = ["as_text", "one_line_reason"]

#: pydantic 给**用户校验器抛出来的错误**套的英文标签，按错误的 ``type`` 认。
#:
#: 校验器里抛 ``ValueError("…")``，报出来的 ``msg`` 是 ``"Value error, …"``；断言失败
#: 是 ``"Assertion failed, …"``。那半句不是判定，是那个库对「这一条来自你的代码」的
#: 排版 —— 读者从 ``type`` 已经知道这件事，而消息本身是本仓写的中文句子。不摘掉的话，
#: 每一条逐行去向都会以半句英文开头。
#:
#: 按 ``type`` 摘，**不按字符串前缀摘**：``type`` 是那个库的接口，标签的措辞是它的
#: 排版。它哪天换一个词，这里就摘不下来 —— 那时留在理由里的是一段噪音，而不是被吃掉
#: 的判定。失败的方向是「多留半句」，不是「少一半话」。
_LABELS: dict[str, str] = {
    "value_error": "Value error, ",
    "assertion_error": "Assertion failed, ",
}


def one_line_reason(error: ValidationError) -> str:
    """把一次校验失败摊成一行：**字段路径 + 消息**，几格之间用 ``"; "`` 接起来。

    带的是字段路径（``body``、``account_id``）而不是**输入值**：路径说的是「哪一格
    不对」，那一格的值调用方自己手上就有。消息里会不会出现输入值由**写校验器的人**定
    （``check_canonical_id`` 就把原值写进了它自己的消息里）—— 这一层不替它决定，也不
    再补一份。

    一条错误都没有的 ``ValidationError`` 在这条路径上构造不出来（pydantic 不会造一个
    空的失败），所以这里没有「空串」这个取值需要处置 —— 真出现的话，调用点拿到的是一
    个空理由，而它看起来与「没有理由」一样。
    """
    parts: list[str] = []
    for item in error.errors():
        message = item["msg"].removeprefix(_LABELS.get(item["type"], ""))
        # 有路径就带上（见模块 docstring 第 1 条）；没有就只说消息。
        if item["loc"]:
            message = f"{'.'.join(str(part) for part in item['loc'])}: {message}"
        parts.append(message)
    return "; ".join(parts)


def as_text(value: Any) -> str:
    """把调用方交来的一个值原样摊成一行文字，**只用于对账**。

    它不做任何规范化：交来的 ``None``、数字、别的东西都照它交来的样子回显 —— 这一格
    要说的是「你交的是这个」，而不是「它应该是什么」。所以它与 ``motherpool.py`` 那个
    同名函数**不是**同一件事（那个要把上游的列值规范成文本），这里刻意不做那件事：
    一条去向里写着 ``None``，读者一眼看得出那一格是空的；写成 ``""`` 之后，它与
    「交上来一个空字符串」在纸上长得一模一样，而两者的下一步不同。

    文本照原样回，非文本用 ``repr``（``None``、``['a']`` 都是那个值在 Python 里的写法）。
    这两支合起来**仍然**把数字 ``1`` 与文本 ``"1"`` 印成同一行字 —— 这是这一格已知的
    极限，写在这里而不是让下一个人以为它分得开：这个参数只用来对账（「我交的那一行在这
    份回执里吗」），不用来认身份，而认身份靠 ``ordinal``。
    """
    return value if isinstance(value, str) else repr(value)

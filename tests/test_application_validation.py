"""``application/validation.py::one_line_reason`` 的判据。

它有两个调用方（``outreach.py`` 的逐条提议、``relations.py`` 的逐行判定），两处的
判据各自在自己的文件里（那一行的去向对不对），这里只判**这一句怎么说**。两件事分开
测的理由与 ``application/relations.py`` 的两半同一条：合成一份会让「渲染对」与
「渲染出来的那句话被放对了地方」互相遮蔽。

## 这里每一条都对应一个**曾经会被静默吃掉**的东西

* 字段路径丢了 —— 理由说「值不对」，而交上来二十行的人无从知道是哪一格；
* 整行级的判据被拼成一句以 ``: `` 开头的话 —— 它读起来像「有一个名字为空的字段」，
  而事实是那条判据管的是整行；
* 渲染自己把**输入值**加进去 —— 那些理由会被读、被留档，而输入值里可能是客户的名字。

前两条是这一处的判据，第三条是这一处**不许做**的事：渲染只搬 ``loc`` 与 ``msg``，
一个字节都不自己加。所以下面的断言用的是**等号**而不是「包含」—— 一个顺手把输入值
补进消息里的实现，在 ``in`` 上完全看不出来。

## 下半：``as_text``（从两个生产者里提出来的那一格）

它回答的是另一个问题（「这一行交上来的原文是什么」），而它与上面那个函数共用一批读者，
所以住在同一个模块里。它自己的判据只有一条：**不做任何规范化**。这一条只有拿它的两个
同名的兄弟当对照才有内容 —— ``motherpool.py::_as_text`` 把 ``None`` 变成 ``""``、把
布尔变成 ``true``（那是给上游的列值用的），``shadow.py::_as_text`` 把一格**校验**成非空
字符串（不合格就抛）。三个名字像，做的事不同，而这一支的全部价值就在「照原文」上：
一条去向里写着 ``None``，读者一眼看得出那一格是空的；写成 ``""`` 之后，它与「交上来一个
空字符串」在纸上长得一模一样，而两者的下一步不同。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError, model_validator

from marketops.application.validation import as_text, one_line_reason
from marketops.domain.base import StrictModel


class TwoFields(StrictModel):
    """缺两格的最小模型 —— 用来取一个**多格**的 ``ValidationError``。"""

    account_id: str
    venue_asset_id: str


class WholeRow(StrictModel):
    """带一条**整行级**判据的最小模型（``@model_validator`` 报出来的错误没有路径）。"""

    name: str = ""

    @model_validator(mode="after")
    def _a_row_without_a_name_is_not_a_row(self) -> "WholeRow":
        if not self.name:
            raise ValueError("这一行没有名字")
        return self


def test_one_field_comes_with_its_path() -> None:
    """一格不对：``字段路径: 消息``。路径是承重的 —— 它是调用方唯一的抓手。"""
    with pytest.raises(ValidationError) as caught:
        TwoFields(account_id="a1")
    assert one_line_reason(caught.value) == "venue_asset_id: Field required"


def test_several_fields_are_joined_into_one_line() -> None:
    """多格用 ``"; "`` 接起来，而**这一层不加别的东西**。

    断言写成「等于两格按那一条规则拼出来的东西」而不是「包含这两句」：后者对一个顺手
    在句尾补一句、或在中间插一格的实现在两个方向上都看不出来。这里没有换行的判据 ——
    校验器自己的消息可以是多行的（本仓的判定话就是这么写的，见下面那条）。
    """
    with pytest.raises(ValidationError) as caught:
        TwoFields()
    assert one_line_reason(caught.value) == (
        "account_id: Field required; venue_asset_id: Field required"
    )


def test_a_whole_row_judgement_carries_no_empty_path() -> None:
    """整行级的判据**没有**路径可指，所以它只说消息。

    照 ``".".join(loc)`` 拼出来会是 ``": 这一行没有名字"`` —— 那句话读起来像「有一个
    名字为空的字段」，而事实是这条判据管的是整行。这一条与上面那一条合起来才是完整的
    渲染规则：**有路径就给路径，没有就不给**，不许给一个空的。
    """
    with pytest.raises(ValidationError) as caught:
        WholeRow(name="")
    assert one_line_reason(caught.value) == "这一行没有名字"


def test_the_pydantic_label_for_a_validator_error_is_dropped() -> None:
    """pydantic 给**校验器自己抛的**错误套的半句英文标签要摘掉，按 ``type`` 摘。

    报出来的 ``msg`` 是 ``"Value error, 这一行没有名字"``，而 ``Value error,`` 不是判定：
    它是那个库对「这一条来自你的代码」的排版。留着它，每一条逐行去向都以半句英文开头
    （「第 3 行为什么没写：Value error, …」）。

    按 ``type`` 摘是这个判据的**要点**（实现注释里写了为什么）：按字符串前缀摘会在
    pydantic 换一个词时静默失效，而 ``type`` 是它的接口。
    """
    with pytest.raises(ValidationError) as caught:
        WholeRow(name="")
    assert caught.value.errors()[0]["msg"].startswith("Value error, "), (
        "这一条判据的输入变了：pydantic 不再给校验器的错误加这个前缀"
    )
    assert one_line_reason(caught.value) == "这一行没有名字"


def test_a_message_that_is_not_the_library_wording_is_left_alone() -> None:
    """形状类错误（``Field required`` 那种）原样报出来 —— 摘的只有那半句标签。

    这一条挡住一种「顺手把英文全清掉」的实现：pydantic 自己的消息是它给的**判据**，
    改写它等于替那个库说话。
    """
    with pytest.raises(ValidationError) as caught:
        TwoFields()
    message = one_line_reason(caught.value)
    assert "Field required" in message
    assert message == "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in caught.value.errors()
    ), "这几条消息里没有可摘的标签，所以渲染就是「路径 + 原消息」"


def test_the_renderer_adds_no_input_value_of_its_own() -> None:
    """渲染搬的是 ``msg``，**不自己补输入值** —— 那些理由会被读、被留档。

    这条用等号判：一个把 ``value`` 补进消息里的实现在「包含」上完全看不出来。

    顺带记下这一层的边界：消息里**会不会**出现输入值由写校验器的人定
    （``check_canonical_id`` 就把原值写进了它自己的消息里，那是它有意为之的口径），
    这一层不替它决定，也不再加一份。
    """
    with pytest.raises(ValidationError) as caught:
        TwoFields(account_id="a1")
    assert one_line_reason(caught.value) == f"venue_asset_id: {caught.value.errors()[0]['msg']}"


# --- ``as_text``：只照原文，不做任何规范化 ---------------------------------------


def test_a_missing_value_is_echoed_as_missing_not_as_empty() -> None:
    """``None`` 回显成 ``None``，**不是**空串 —— 这与 ``motherpool.py`` 那个同名函数
    正相反（那个把 ``None`` 规范成 ``""``）。

    这一条是这一支存在的理由：交上来的那一格是空的，与交上来一个空字符串，在**下一步**
    上不是同一件事（前者是「这格没填」、后者是「这格填了空」），而回执只有在这一格照原文
    写的时候才分得开。
    """
    assert as_text(None) == "None"
    assert as_text("") == ""
    assert as_text(None) != as_text("")


def test_the_echo_does_not_normalize_anything_else_either() -> None:
    """布尔与清单也照 Python 的写法回 —— 没有 ``true``、没有逗号拼接。

    逐个写死而不是「包含」：一个把这几格顺手规范化的实现在这两个断言上都得红，而
    「包含」判不出 ``True`` 与 ``true`` 的区别。

    文本那一支也要走一遍：一条去向里印着 ``'acct-1'`` 的话，多出来的两个引号会被读成
    「这一格的值里有引号」—— 这是这一支唯一会错的地方，而它只在文本上看得出来。
    """
    assert as_text(True) == "True"
    assert as_text(0) == "0"
    assert as_text(["a", "b"]) == "['a', 'b']"
    assert as_text("acct-1") == "acct-1"

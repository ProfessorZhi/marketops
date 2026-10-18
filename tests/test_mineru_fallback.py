"""MinerU fallback 的「能不能用」判据，以及 ``default_router`` 接不接它的决定。

## 「装了吗」与「能用吗」是两个问题

``mineru_available()`` 答前者：重依赖能不能 import。``mineru_fallback_ready()``
答后者：这个 fallback 能不能真的产出一份文档。今天两个都是 ``False``，但理由是
**不同的**两半 —— 而 ``pip install mineru`` 只会让前一个翻真。

那一刻的状态是最坏的一种：能力表会报「就绪」、``DocumentRouter`` 会把它接上，
而 ``_run_mineru`` 仍然无条件抛 ``ParserUnavailableError`` —— 于是每一份复杂 PDF
的结论都是「用 MinerU 也读不出来」，而真相是「MinerU 根本没被接对一个调用点」。
这个文件把那一刻钉住：**依赖装上了也不接**，而且理由说得出来。

## 判据怎么造出「依赖装上了」

直接改 ``_mineru_do_parse`` 与 ``_CALL_POINT_CALIBRATED`` 两个模块级事实，不去
patch 那个谓词本身 —— patch 谓词只会得到「router 调用了我给它的函数」这个
同义反复，而那正是要防的漂移。
"""

from __future__ import annotations

import pytest

from marketops.adapters import mineru_adapter
from marketops.interfaces.composition import default_router

# ----------------------------------------------------------------------
# 两个问题
# ----------------------------------------------------------------------
def test_both_halves_of_the_predicate_are_read_from_the_module_facts() -> None:
    """先钉住两个谓词在本机的真实取值，后面每一条都从这个起点出发。

    正控在这里是必须的：下面每一条都在改这两个事实，若它们本来就是另一种取值，
    那些用例就在测别的东西。
    """
    assert mineru_adapter._mineru_do_parse is None, (
        "本机装了 mineru —— 这一组用例的前提变了，请重新确认下面每一条的期望"
    )
    assert mineru_adapter.mineru_available() is False
    assert mineru_adapter.mineru_fallback_ready() is False


def test_an_installed_dependency_is_not_enough_to_make_the_fallback_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """依赖可 import 之后，``available`` 翻真而 ``fallback_ready`` **仍然是假**。

    这一条是整组的核心：两半必须各答各的，否则「装了吗」这个格子会顺带回答
    「能用吗」，而它对后半截一无所知。
    """
    monkeypatch.setattr(mineru_adapter, "_mineru_do_parse", object)

    assert mineru_adapter.mineru_available() is True
    assert mineru_adapter.mineru_fallback_ready() is False, (
        "依赖装上了就叫「能用」—— 而 _run_mineru 无条件抛"
    )
    blocker = mineru_adapter.mineru_fallback_blocker()
    assert "调用点" in blocker and "校准" in blocker
    assert "不可 import" not in blocker, "理由指错了那一半"


def test_the_blocker_names_whichever_half_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """两种失败各有一句话，而且**互斥**。

    合成一句「MinerU 不可用」会让「去装依赖」与「去校准调用点」同形 —— 前者是
    运维能自己做的，后者需要一个真实样本才能做。
    """
    assert "不可 import" in mineru_adapter.mineru_fallback_blocker()

    monkeypatch.setattr(mineru_adapter, "_mineru_do_parse", object)
    monkeypatch.setattr(mineru_adapter, "_CALL_POINT_CALIBRATED", True)
    assert mineru_adapter.mineru_fallback_ready() is True
    assert mineru_adapter.mineru_fallback_blocker() == "", "能用时不该还有 blocker"


def test_the_two_ways_a_fallback_can_be_unusable_read_differently(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """同一个异常类，两句话，**互为对照**。

    依赖没装与调用点没校准都抛 ``ParserUnavailableError``，处置却是两个人的活：
    去装依赖 vs 拿一份真实样本校准调用点。所以这两句必须分得开。

    这条判据必须成对写。只写「装了时说的是调用点」时，把 ``parse`` 开头那个
    ``available`` 检查整段删掉它照样绿 —— 因为那时 ``_run_mineru`` 会抛出同一句话，
    两半合起来才是「这两句话确实来自两个不同的分支」。

    另一半同样重要：一个「看起来能跑」的调用点会把 PDF 解析成没人验证过的
    markdown，而它会一路走进证据与报告 —— 比明确地不接它坏得多。
    """
    from marketops.domain.errors import ParserUnavailableError

    parser = mineru_adapter.MinerUDocumentParser(tmp_path / "mineru")

    with pytest.raises(ParserUnavailableError) as missing:
        parser.parse(tmp_path / "sample.pdf")
    assert "未安装" in str(missing.value)

    monkeypatch.setattr(mineru_adapter, "_mineru_do_parse", object)
    with pytest.raises(ParserUnavailableError) as uncalibrated:
        parser.parse(tmp_path / "sample.pdf")

    text = str(uncalibrated.value)
    assert "调用点" in text and "校准" in text, "装了依赖就该说下一步是校准"
    assert "未安装" not in text, "依赖明明装上了，却报成了没装"


# ----------------------------------------------------------------------
# default_router 接不接它
# ----------------------------------------------------------------------
def test_the_router_does_not_wire_a_fallback_that_cannot_produce_a_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """依赖装上了，``default_router`` **仍然不接** MinerU。

    这是整个 D7 的落点：接与不接读的是 ``mineru_fallback_ready()``，不是
    ``mineru_available()``。接上一个每次解析都抛的 fallback，等于把「这份 PDF
    读不出来」写成「这份 PDF 用 MinerU 也读不出来」。
    """
    monkeypatch.setattr(mineru_adapter, "_mineru_do_parse", object)

    router = default_router(mineru_output_dir=tmp_path / "mineru")
    assert router.parser_names == ["html", "pypdf"], (
        "接上了一个产不出文档的 fallback"
    )


def test_a_usable_fallback_is_wired(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """正控：两半都成立时它**确实**被接上。

    没有这一条，上面那句「没接」在一个恒不接 MinerU 的实现上同样成立 —— 而那
    会让 ``pip install mineru`` 加上校准永远不起作用。
    """
    monkeypatch.setattr(mineru_adapter, "_mineru_do_parse", object)
    monkeypatch.setattr(mineru_adapter, "_CALL_POINT_CALIBRATED", True)

    router = default_router(mineru_output_dir=tmp_path / "mineru")
    assert router.parser_names == ["html", "pypdf", "mineru"]


def test_no_output_directory_means_no_fallback_even_when_it_is_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没给中间产物目录就不接：MinerU 必须落在配置指定的位置，不得默认写盘。

    一条会自己挑目录写文件的解析路径，会让「这些中间产物是谁的、能不能删」这个
    问题无法从配置回答。
    """
    monkeypatch.setattr(mineru_adapter, "_mineru_do_parse", object)
    monkeypatch.setattr(mineru_adapter, "_CALL_POINT_CALIBRATED", True)

    assert default_router(mineru_output_dir=None).parser_names == ["html", "pypdf"]


def test_the_capability_row_uses_the_same_judgement_as_the_wiring(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``get_capabilities`` 的 mineru 行与 ``default_router`` 读的是**同一条**判据。

    两个格子各有一个问题要答（``runtime_ready`` 是「装了没」），但 ``blocker``
    必须覆盖「能不能用」—— 否则依赖装上的那一刻，能力表会报一行没有 blocker 的
    「就绪」，而抓取路径根本没有接它。三处（谓词、接线、能力表）读一份判据。
    """
    from marketops.adapters.mcp_server.tools import control_handlers

    monkeypatch.setattr(mineru_adapter, "_mineru_do_parse", object)

    caps = control_handlers(settings=settings, wired=frozenset({"fetch_document"}))[
        "get_capabilities"
    ]()
    row = next(item for item in caps["integrations"] if item["name"] == "mineru")

    assert row["runtime_ready"] is True, "依赖装上了就要如实说装了"
    assert row["blocker"], "装了却不能用时，那一行不能看起来一切正常"
    assert "校准" in row["blocker"]
    assert default_router(mineru_output_dir=settings.mirrors_dir / "mineru").parser_names == [
        "html",
        "pypdf",
    ]

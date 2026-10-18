"""文档解析路由的 fallback 分支（``application/document_router.py``）。

## 这个文件补的是什么

``DocumentRouter`` 有三条分支在它落地时**一条判据都没有**：primary 失败之后
fallback 成功了、fallback 不可用、压根没配 fallback。那时没有 MinerU 样本，也没
有人接到过这三条路上 —— 于是「先轻量后 fallback，且失败要说得出为什么」这句需求
原文，在这里只有一份实现，没有一条执行器。

全部用**替身 parser**：本文件不碰磁盘、不 import pypdf 也不 import MinerU。
判据是纯策略的（给定「谁能解析、谁成功、谁失败」，产出的是什么），注入真依赖
只会让它依赖本机装了什么。

## 失败是一个结果，不是一次崩溃

第 3 条需求原文：fallback 不可用时**返回 primary 的失败信息 + 警告**，不阻塞
pipeline。一条解析不了的 PDF 不该让整个账号的 run 失败 —— 但它也**不该**看起来
像「这份 PDF 用两种 parser 都试过了、都不行」。所以下面既测「抛了」也测
「那句话里有没有 fallback 的情形」。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marketops.application.document_router import DocumentRouter
from marketops.domain.enums import SourceKind
from marketops.domain.errors import ParseError, ParserUnavailableError
from marketops.ports.documents import DocumentLink, DocumentParser, ParsedDocument


class FakeParser(DocumentParser):
    """一个可编程的 parser：能不能处理、成功还是失败、失败成哪一种。"""

    extracts_links = False

    def __init__(
        self,
        name: str,
        *,
        can_parse: bool = True,
        text: str = "",
        raises: BaseException | None = None,
        links: tuple[DocumentLink, ...] = (),
    ) -> None:
        self.name = name
        self.source_kind = SourceKind.FETCHED_HTML
        self._can_parse = can_parse
        self._text = text
        self._raises = raises
        self._links = links
        self.calls: list[Path] = []

    def can_parse(self, path: Path, *, content_type: str = "") -> bool:
        return self._can_parse

    def parse(self, path: Path, *, content_type: str = "") -> ParsedDocument:
        self.calls.append(path)
        if self._raises is not None:
            raise self._raises
        return ParsedDocument(
            source_path=path,
            sha256="b" * 64,
            media_type="text/html",
            parser=self.name,
            title="",
            text=self._text or f"由 {self.name} 解析出的正文",
            links=self._links,
            extracts_links=self.extracts_links,
            source_kind=self.source_kind,
        )


PDF = Path("document.pdf")


# ----------------------------------------------------------------------
# 顺序：轻量在前，fallback 只在失败之后
# ----------------------------------------------------------------------
def test_a_working_primary_never_reaches_the_fallback() -> None:
    """primary 成功时 fallback **一次都不被调用**。

    需求原文「不要所有 PDF 默认跑 MinerU」在这里的可测形态就是这一条 —— 它是
    成本判据（MinerU 是数 GB 的模型）也是正确性判据（第二条解析路径意味着第二份
    正文，而下游分不出用的是哪一份）。
    """
    primary = FakeParser("primary", text="轻量解析出的正文")
    fallback = FakeParser("fallback")
    document = DocumentRouter([primary], [fallback]).parse(PDF)

    assert fallback.calls == []
    assert document.parser == "primary"
    assert document.used_fallback is False
    assert document.fallback_reason == ""


def test_a_failing_primary_hands_over_to_the_fallback() -> None:
    """正控：primary 抛 ``ParseError`` 之后，fallback **确实**被调用并采纳。

    没有这一条，上面那句「一次都不被调用」在 fallback 整个没接上时同样成立。
    """
    primary = FakeParser("primary", raises=ParseError("扫描件，提不出文本"))
    fallback = FakeParser("fallback", text="fallback 解析出的正文")
    document = DocumentRouter([primary], [fallback]).parse(PDF)

    assert fallback.calls == [PDF]
    assert document.parser == "fallback"
    assert document.text == "fallback 解析出的正文"
    assert document.used_fallback is True
    assert document.fallback_reason == "primary -> fallback"


def test_the_adopted_fallback_keeps_its_links() -> None:
    """采纳 fallback 的结果时，它的链接跟着一起过来。

    路由在这一处**重建**了一个 ``ParsedDocument``（要改 ``used_fallback`` 与
    ``fallback_reason`` 两个字段），于是每一个新字段都有一次被忘掉的机会 ——
    而忘掉的后果是「这份 PDF 经过 MinerU 之后没有链接了」，读起来是一个结论。
    成对写：下面那一条是 primary 成功时用的是**它自己**的链接。
    """
    primary = FakeParser("primary", raises=ParseError("扫描件"))
    fallback = FakeParser(
        "fallback", links=(DocumentLink(url="/a", text="A"), DocumentLink(url="/b"))
    )
    document = DocumentRouter([primary], [fallback]).parse(PDF)

    assert [link.url for link in document.links] == ["/a", "/b"]


def test_a_working_primary_keeps_its_own_links() -> None:
    """另一半：primary 成功时采纳的是 primary 的链接，不是 fallback 的。

    没有这一条时，一个「不管谁解析都把 fallback 的链接填上去」的实现能让上面
    那条通过 —— 而它比丢字段更坏：报告上会有一条来自从未被采纳的那条解析路径
    的链接。
    """
    primary = FakeParser("primary", links=(DocumentLink(url="/from-primary"),))
    fallback = FakeParser("fallback", links=(DocumentLink(url="/from-fallback"),))
    document = DocumentRouter([primary], [fallback]).parse(PDF)

    assert [link.url for link in document.links] == ["/from-primary"]


def test_the_first_primary_that_can_parse_wins() -> None:
    """多个 primary 时按声明顺序取第一个 ``can_parse`` 为真的。

    顺序是这份配置唯一的表达方式 —— 一个「谁都行」的实现会在两个 parser 都能
    处理同一份文件时产出不确定的正文，而报告上两次运行的差异会被归因到内容本身。
    """
    first = FakeParser("first", can_parse=False)
    second = FakeParser("second")
    document = DocumentRouter([first, second]).parse(PDF)

    assert first.calls == []
    assert document.parser == "second"


def test_a_failure_that_is_not_a_parse_error_is_not_swallowed() -> None:
    """**只有** ``ParseError`` 会被翻译成「换个 parser 再试」。

    ``ParseError`` 说的是「这份文件我读不了」（可分类，SOP L119 有处置）；
    一个 ``ValueError`` 说的是「这段代码有 bug」。把后者也吞掉，会让一个真的
    缺陷表现成「这份 PDF 有点难」。
    """
    primary = FakeParser("primary", raises=ValueError("bug"))
    fallback = FakeParser("fallback")
    with pytest.raises(ValueError):
        DocumentRouter([primary], [fallback]).parse(PDF)
    assert fallback.calls == [], "把一次代码缺陷当成了「换一个 parser 再试试」"


# ----------------------------------------------------------------------
# 没有 parser 能用：两种「失败」，两种话
# ----------------------------------------------------------------------
def test_nothing_can_parse_the_file_says_which_parsers_were_registered() -> None:
    """没有任何 parser 声称能处理时，那句话要**列出已注册的**。

    不列出来时，「没有 parser 能处理」与「配置里少了一个 parser」在报告上同形，
    而后者才是常见的那个原因。
    """
    with pytest.raises(ParseError) as raised:
        DocumentRouter([FakeParser("light", can_parse=False)]).parse(PDF)
    assert "没有 parser 能处理" in str(raised.value)
    assert "light" in str(raised.value)


def test_a_primary_failure_with_no_fallback_still_says_there_was_no_fallback() -> None:
    """primary 失败、又没配 fallback ⇒ 抛出，且那句里要有「没有 fallback」这个事实。

    只说 primary 的失败原因，会让「这份文件两个 parser 都试过了」与「只试了一个、
    另一个根本没接」在报告上同形 —— 而这两件事的处置完全不同（换来源 vs 装依赖）。
    """
    primary = FakeParser("primary", raises=ParseError("扫描件，提不出文本"))
    with pytest.raises(ParseError) as raised:
        DocumentRouter([primary]).parse(PDF)

    text = str(raised.value)
    assert "扫描件，提不出文本" in text, "primary 的原因要留下 —— 它是唯一可分类的那条信息"
    assert "fallback" in text
    assert "未配置任何 fallback" in text


def test_a_fallback_that_is_unavailable_does_not_block_the_pipeline() -> None:
    """fallback 不可用 ⇒ 仍然是 ``ParseError``（不阻塞 pipeline），但要说清为什么。

    需求原文第 3 条：一条解析不了的 PDF 不该让整个账号的 run 失败。同时那句
    「不可用」必须留下 —— 否则「MinerU 没装」与「MinerU 装了但读不了这份 PDF」
    同形，而它们的处置是「装依赖」与「换来源」。
    """
    primary = FakeParser("primary", raises=ParseError("扫描件"))
    fallback = FakeParser("mineru", raises=ParserUnavailableError("MinerU 未安装"))
    with pytest.raises(ParseError) as raised:
        DocumentRouter([primary], [fallback]).parse(PDF)

    text = str(raised.value)
    assert "mineru" in text
    assert "不可用" in text
    assert "MinerU 未安装" in text


def test_a_fallback_that_fails_is_reported_differently_from_one_that_is_missing() -> None:
    """fallback 试了但失败 ⇒ 说「失败」，不说「不可用」。

    两种情形在 ``DocumentRouter`` 里各有一句话，而这句话是排查时唯一能区分
    「装错了」与「这份文件真的读不了」的东西。
    """
    primary = FakeParser("primary", raises=ParseError("扫描件"))
    fallback = FakeParser("mineru", raises=ParseError("MinerU 未产出内容"))
    with pytest.raises(ParseError) as raised:
        DocumentRouter([primary], [fallback]).parse(PDF)

    text = str(raised.value)
    assert "失败" in text
    assert "不可用" not in text
    assert "MinerU 未产出内容" in text


def test_a_fallback_that_cannot_parse_is_skipped_and_named_as_unconfigured() -> None:
    """``can_parse`` 为假的 fallback 连试都不试，且那句话要说「没配」而不是「失败」。

    它被跳过是因为它声称自己处理不了这份文件 —— 那是**正常的**，不是一次失败。
    把两者混起来会让「MinerU 不处理 HTML」看起来像 MinerU 出了问题。
    """
    primary = FakeParser("primary", raises=ParseError("扫描件"))
    fallback = FakeParser("mineru", can_parse=False)
    with pytest.raises(ParseError) as raised:
        DocumentRouter([primary], [fallback]).parse(PDF)

    assert fallback.calls == []
    assert "未配置任何 fallback" in str(raised.value)


def test_the_parser_names_list_everything_registered_including_fallbacks() -> None:
    """``parser_names`` 报的是**全部**已注册的，与哪一份文件无关。

    它是 ``get_capabilities`` 与排查时「这个部署到底接了几个 parser」的唯一来源，
    所以不能按 can_parse 过滤。
    """
    router = DocumentRouter([FakeParser("html"), FakeParser("pdf")], [FakeParser("mineru")])
    assert router.parser_names == ["html", "pdf", "mineru"]

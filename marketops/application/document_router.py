"""文档解析路由：先轻量，后 fallback。

需求原文：**不要所有 PDF 默认跑 MinerU。**
普通文本 PDF → 轻量 parser；复杂 layout / scanned PDF → MinerU fallback。

本模块把这个策略实现为「有序候选 + 明确失败信号」：

1. 按顺序找第一个 ``can_parse`` 为真的 primary parser。
2. primary 抛 ``ParseError``（例如判定为扫描件）→ 记下原因，试 fallback。
3. fallback 不可用 → **返回 primary 的失败信息 + 警告**，不阻塞 pipeline。
   一条解析不了的 PDF 不应该让整个账号的 run 失败。

第 3 条是刻意的：本阶段 MinerU 未安装，若此处抛错会让「扫描件」变成
「整个 run 失败」，从而在 §10 指标里被误记成 blocked/failed。

## 它为什么住在 ``application/`` 而不是 ``adapters/``（B7a）

本模块从 ``adapters/document_router.py`` 搬来。判据是 ``adapters/__init__.py``
开头那一句：**把它 import 进来会不会需要磁盘、网络或一个进程？**

``DocumentRouter`` 不会。它的构造参数全是 ``DocumentParser`` —— 一个端口 ——
它做的事是"按顺序试，失败了记原因再试下一个"。这与 ``application/escalation.py``
（抓取结果 → 是否升级到浏览器）**同形**：都是纯策略，输入输出都是领域对象。

原来它被放进 ``adapters/`` 是因为**邻居**：同一个文件里还住着 ``default_router()``,
而那个函数真的会去 import ``pypdf`` / ``MinerU``。但这恰恰是「一个模块里住着两种
东西」的典型样子 —— 策略与接线被文件名捆在一起，于是策略被迫跟接线一起承受
adapter 的判据。拆开之后，``default_router()`` 去了 ``marketops/interfaces/composition.py``
（组合根，本来就该在那里），策略留在这里。

拆开这件事有一个可验证的后果：``pipeline`` 不再 import ``adapters/`` 了
（搬之前它 ``from ..adapters.document_router import DocumentRouter``，而那一条
按传递闭包算下去真的通到 ``pypdf`` 与 MinerU）。这条**现在还没有执行器** ——
B7a-3 要在 ``tests/test_adapters_hold_the_vendors.py`` 里补上传递闭包扫描，
在那之前它只是我用 AST 扫过一遍的观察，不是一条会自动变红的话。
"""

from __future__ import annotations

from pathlib import Path

from ..domain.errors import ParseError, ParserUnavailableError
from ..ports.documents import DocumentParser, ParsedDocument


class DocumentRouter:
    """按优先级尝试多个 parser。

    Args:
        parsers: 主解析器，按优先级排列（轻量在前）。
        fallbacks: 兜底解析器（如 MinerU）。不可用时被跳过并记录。
    """

    def __init__(
        self,
        parsers: list[DocumentParser],
        fallbacks: list[DocumentParser] | None = None,
    ) -> None:
        self._parsers = list(parsers)
        self._fallbacks = list(fallbacks or [])

    @property
    def parser_names(self) -> list[str]:
        return [parser.name for parser in (*self._parsers, *self._fallbacks)]

    def parse(self, path: Path, *, content_type: str = "") -> ParsedDocument:
        """解析文件。

        Raises:
            ParseError: 没有任何 parser 能处理该文件，或全部失败且无可用 fallback。
        """
        primary: DocumentParser | None = None
        primary_error: ParseError | None = None

        for parser in self._parsers:
            if not parser.can_parse(path, content_type=content_type):
                continue
            primary = parser
            try:
                return parser.parse(path, content_type=content_type)
            except ParseError as exc:
                primary_error = exc
                break

        if primary is None:
            raise ParseError(
                f"没有 parser 能处理该文件：{path.name}（content_type={content_type!r}）。"
                f"已注册：{self.parser_names}"
            )

        assert primary_error is not None  # 上面 break 时必然已赋值

        unavailable: list[str] = []
        for fallback in self._fallbacks:
            if not fallback.can_parse(path, content_type=content_type):
                continue
            try:
                document = fallback.parse(path, content_type=content_type)
            except ParserUnavailableError as exc:
                unavailable.append(f"{fallback.name}(不可用: {exc})")
                continue
            except ParseError as exc:
                unavailable.append(f"{fallback.name}(失败: {exc})")
                continue
            return ParsedDocument(
                source_path=document.source_path,
                sha256=document.sha256,
                media_type=document.media_type,
                parser=document.parser,
                title=document.title,
                text=document.text,
                # 采纳 fallback 的文档时**逐字搬过来**：这里重建的是「谁产的、
                # 算不算 fallback」两个字段，不是内容。漏掉这一句的后果是
                # 「这份 PDF 经过 MinerU 之后没有链接了」，报告上读起来像结论。
                links=document.links,
                extracts_links=document.extracts_links,
                source_kind=document.source_kind,
                page_count=document.page_count,
                used_fallback=True,
                fallback_reason=f"{primary.name} -> {fallback.name}",
                warnings=document.warnings,
            )

        detail = "; ".join(unavailable) if unavailable else "未配置任何 fallback parser"
        raise ParseError(f"{primary_error} | fallback 情形：{detail}")


__all__ = ["DocumentRouter"]

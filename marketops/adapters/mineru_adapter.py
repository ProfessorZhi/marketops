"""MinerU fallback adapter —— **契约已完成，重依赖按需启用**。

本轮（Windows Demo 初始化）**不安装** MinerU 重依赖（torch 等，数 GB）与模型权重。
理由：没有真实复杂 PDF 样本时，装了也无从验证，只会拖慢每次环境重建。

因此本模块的存在方式是：
* 实现完整的 ``DocumentParser`` 契约（``can_parse`` / ``parse``）；
* 未安装时 ``available`` 为 False，``parse`` 抛 ``ParserUnavailableError``；
* **不**在 import 期失败 —— 缺重依赖不应该让整个测试套件挂掉。

启用方式（有真实样本后）：
    pip install -e ".[mineru]"
然后由 ``marketops.interfaces.composition.default_router`` 把本 adapter 作为 fallback 接上。
"""

from __future__ import annotations

from pathlib import Path

from ..domain.enums import SourceKind
from ..domain.errors import ParserUnavailableError
from .hashing import sha256_file
from ..ports.documents import DocumentParser, ParsedDocument

try:  # pragma: no cover - 取决于本地是否装了可选重依赖
    from mineru.cli.common import do_parse as _mineru_do_parse  # type: ignore

    _MINERU_IMPORT_ERROR = ""
except Exception as exc:  # noqa: BLE001 - 任何 import 期失败都等价于「不可用」
    _mineru_do_parse = None
    _MINERU_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


#: ``_run_mineru`` 这个**调用点**是否已经按真实样本校准过。
#:
#: 今天恒为 ``False``，而它不是一个「还没做完」的标记：MinerU 的 Python API 在
#: 小版本间变动较大，在一个没有真实复杂 PDF 样本的环境里猜出来的调用，产出的
#: markdown 对不对**没有人能判断**。写一个看起来能跑、实际把 PDF 解析成垃圾的
#: 调用点，比明确地不接它更坏 —— 前者会一路走进报告，后者只是少一条能力。
_CALL_POINT_CALIBRATED = False


def mineru_available() -> bool:
    """重依赖能不能 import —— 问的是「**装了没**」。

    这是 ``get_capabilities`` 里 ``runtime_ready`` 那一格的答案，也只有它是。
    """
    return _mineru_do_parse is not None


def mineru_unavailable_reason() -> str:
    """上面那个问题的理由。"""
    return _MINERU_IMPORT_ERROR or "ok"


def mineru_fallback_ready() -> bool:
    """这个 fallback 能不能**真的产出一份文档** —— 问的是「**能不能用**」。

    与 ``mineru_available()`` 是两个问题，而它们的答案今天都是 False，靠的是
    不同的理由。``pip install mineru`` 会让 ``mineru_available()`` 翻真，而
    ``_run_mineru`` 仍然无条件抛 ``ParserUnavailableError`` —— 于是那一格会
    报「就绪」、``DocumentRouter`` 会把它接上，每一次解析都失败。

    ``default_router()`` 问的是这**一个**（要不要接上它），``get_capabilities``
    的两个格子各问一个。两处各判一次同一个问题的时代结束了：判据只有一份，
    在这一层。
    """
    return mineru_available() and _CALL_POINT_CALIBRATED


def mineru_fallback_blocker() -> str:
    """不能用的理由，**按问到的哪一半**分句。空串表示能用。"""
    if not mineru_available():
        return f"不可 import：{_MINERU_IMPORT_ERROR}"
    if not _CALL_POINT_CALIBRATED:
        return (
            "依赖已装，但调用点尚未按真实样本校准"
            "（_run_mineru 无条件抛 ParserUnavailableError）——"
            "接上去的结果是每一次解析都失败，所以不接"
        )
    return ""


class MinerUDocumentParser(DocumentParser):
    """复杂 layout / 扫描件 PDF 的 fallback 解析器。

    **本解析器不看链接**（``extracts_links = False``）。MinerU 产出的是 markdown，
    而 markdown 里的 ``[文字](地址)`` 是抽得出来的 —— 但那要等调用点校准之后才有
    第一份**真的** markdown 可以对着写，在那之前从印象出发写的抽取规则会产出一批
    没人验证过的地址，而它们会一路走进证据。所以现在明说不抽。

    Args:
        output_dir: MinerU 的中间产物目录。必须由配置注入（通常在
            ``settings.mirrors_dir`` 之下），不得写死。
    """

    name = "mineru"
    source_kind = SourceKind.FETCHED_PDF
    extracts_links = False

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir

    @property
    def available(self) -> bool:
        return mineru_available()

    def can_parse(self, path: Path, *, content_type: str = "") -> bool:
        if "pdf" in content_type.lower():
            return True
        return path.suffix.lower() == ".pdf"

    def parse(self, path: Path, *, content_type: str = "") -> ParsedDocument:
        if not self.available:
            raise ParserUnavailableError(
                "MinerU 未安装，无法执行 fallback 解析。\n"
                f"原因：{mineru_unavailable_reason()}\n"
                "MinerU 重依赖按需启用 —— 有真实复杂 PDF 样本后再装。"
            )

        self._output_dir.mkdir(parents=True, exist_ok=True)

        # 窄接口：MinerU 的 Python API 在小版本间变动较大。把调用收敛在这里，
        # adapter 对外契约（ParsedDocument）保持稳定。
        markdown = _run_mineru(path, self._output_dir)
        if not markdown.strip():
            from ..domain.errors import ParseError

            raise ParseError(f"MinerU 未产出内容（{path.name}）")

        return ParsedDocument(
            source_path=path,
            sha256=sha256_file(path),
            media_type="application/pdf",
            parser=self.name,
            # 同 ``TextPdfParser``：产出的是一份 markdown，它没有「这一页给自己的
            # 名字」这一格 —— 第一个标题行是正文的一部分，不是材料的标题。
            title="",
            text=markdown,
            # 这一段今天到不了（``_run_mineru`` 无条件抛），但字段是必填的：
            # 调用点校准之后，这里的空链接必须仍然读成「没抽」而不是「没有链接」。
            links=(),
            extracts_links=self.extracts_links,
            source_kind=self.source_kind,
            used_fallback=True,
            fallback_reason="mineru",
        )


def _run_mineru(path: Path, output_dir: Path) -> str:
    """调用 MinerU 并返回 markdown 文本。

    单独成函数是为了让「调用点」与「契约」分离：MinerU 升级时只需要改这里。
    """
    raise ParserUnavailableError(
        "MinerU adapter 的调用点尚未按实际样本校准。\n"
        "本轮只交付 adapter contract；装好 mineru 并有真实复杂 PDF 样本后，"
        "在此处按当时版本的 API 实现调用，并用 tests/fixtures 中的真实样本验证。"
    )


__all__ = [
    "MinerUDocumentParser",
    "mineru_available",
    "mineru_fallback_blocker",
    "mineru_fallback_ready",
    "mineru_unavailable_reason",
]

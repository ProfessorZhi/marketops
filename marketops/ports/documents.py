"""``DocumentParser`` 抽象。

**不要所有 PDF 默认跑 MinerU。** 解析分两档：

* 普通文本型 PDF / HTML → 轻量 parser（pypdf / 正则抽取的 HTML），毫秒级、无重依赖。
* 复杂 layout / 扫描件 → MinerU fallback，重依赖 + 模型推理，只在确有必要时启用。

路由逻辑在 ``application/document_router.py``（纯策略：只认本模块的契约）。
接线在 ``interfaces/composition.py``（决定「装哪几个 parser」）。本模块只定义契约。

实现（pypdf / MinerU）在 ``marketops/adapters/``（``text_pdf.py`` /
``mineru_adapter.py``）。本模块**不得**出现任何厂商名。

## 链接：一条必须逐字记下来的字段

``docs/MCP_SERVICE.md`` §5 要求每一页记录它的**链接**。链接有两个不能混的形状：

* **原文**：``href`` 写的是什么（``/contact-us``、``contact``、``https://…``）。
* **转换后的地址**：按页面 URL（和文档里的 ``<base href>``）解析出来的绝对地址。

本模块记的是**前者**，因为 ``parse(path, *, content_type)`` 只知道字节 —— 它既
不知道这份字节是哪个 URL 取回来的，也看不到页面 URL 后面那一半。同一份
``<a href="contact">`` 在两个 URL 下指的是两条不同的链接。所以转换属于**同时知道
文档与 URL 的那一层**（服务面的回执），而原文在这里永不丢失。

``DocumentLink.url`` 因此是逐字值，判据在 ``tests/test_parsers_report_links.py``
（其中一条专门会在「有人顺手把它解析成绝对地址」时转红）。

## 写侧：``PageCopier`` 与 ``DocumentParser`` 是同一份材料的两个方向

:class:`DocumentParser` 读的是「一份已经躺在盘上的字节」，而 :data:`PageCopier`
写的是「把一份已经躺在盘上的字节放到**另一个主体**的落点上」。两者都只认字节与
路径，都不认识镜像目录长什么样 —— 那是 ``adapters/page_mirror.py`` 一处定义的约定。

它在这里而不是在那儿，是因为**方向**：``application/`` 只能 import ``ports``，
而 ``application/reuse.py`` 的台账要拿着它去做一次跨主体的共享。形状做成一个
``Callable`` 别名而不是 ABC，与 ``ports/pool.py`` 的三个合并函数逐字同一条理由 ——
这条端口上没有别的动词，写成类只会多出一个「看起来设计完了」的壳。

``None`` **不是**「省掉这一步」而是「这个部署落不下第二份」：那种部署一次都不该
共享，理由写在 ``application/reuse.py`` 的模块 docstring 里。所以取值范围是
``PageCopier | None`` 而不是「可选参数」。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..domain.enums import SourceKind

#: 把一个镜像文件的字节落进**另一个**主体的目录：``(source, scope, url) -> 落点``。
#:
#: **判据由 ``application/reuse.py`` 给出**（什么时候该共享、共享给谁），实现由
#: ``adapters/page_mirror.py::page_copier`` 给出（落在哪个目录、文件名怎么算）。
#: 与 ``ports/pool.py`` 的三个合并函数同一条边界：存储只负责执行，不负责决定。
#:
#: 中间那一个参数是**接收方的主体 id**，不是路径的一段 —— 路径怎么拼由实现决定，
#: 调用方给的是它的语义（「这些字节从今往后属于这个主体」）。
PageCopier = Callable[[Path, str, str], Path]


@dataclass(frozen=True)
class DocumentLink:
    """文档里的一条链接，**按文档里写的那样**。

    ``url`` 是 ``href`` 的逐字值（去掉了首尾空白），不做解析 —— 理由见模块
    docstring。``text`` 是锚文本（空白折叠后）；图片链接、图标按钮给出的空串是
    一个**真实取值**，不是缺失：这类链接指向的地址正是最有用的那一半。
    """

    url: str
    text: str = ""


@dataclass(frozen=True)
class ParsedDocument:
    """解析结果。

    ``text`` 是后续 literal extraction 的唯一输入。``sha256`` 指向**文件字节**，
    让证据可以回溯到具体的镜像文件。

    ``links`` **必填、没有默认值**：一个安静的 ``()`` 会被读成「这份文档没有
    链接」，而它同样能是「这个 parser 没抽」。两者的处置相反（前者是一份关于
    文档的结论，后者是一条待补的欠账），所以每个 parser 都必须自己写出来。

    ``extracts_links`` 是那个「没抽」标记本身，而且它**是文档的字段而不是解析器
    的属性** —— 读这份文档的人（服务面的回执）手里只有文档：``document_router``
    在采纳 fallback 时会**重建**一个 ``ParsedDocument``，那一刻解析器对象就不在
    手边了，只有 ``parser`` 这个名字跟着过来。字段必填（没有默认值），于是每一处
    构造点都必须表态，而 ``DocumentParser.extracts_links`` 是那句表态的**唯一**
    来源 —— 每个实现都写成 ``extracts_links=self.extracts_links``，两处因此不可能
    各说各的。

    ``title`` **必填、没有默认值**，理由与 ``extracts_links`` 逐字相同：``""``
    有两种读法（「这份材料自己没写标题」与「这个解析器没去取它」），而这两件事的
    处置相反。它今天有一个消费者 —— ``application/discovery_run.py`` 拿它当候选项
    的**指称对象**（这一页给自己的名字），所以那两种读法分开不是洁癖：
    「这一页没有标题」是一条关于材料的读数，而「没人去看」是一条待补的欠账。

    它**不是** ``text`` 的摘要，也不是文档级证据的 ``literal_value``：那一行的字面
    是**整篇正文**（理由见 ``application/evidence_ingest.py``），而这一格是这份材料
    给自己的名字。两者各落各的证据行，各有各的 locator。
    """

    source_path: Path
    sha256: str
    media_type: str
    parser: str
    title: str
    text: str
    links: tuple[DocumentLink, ...]
    extracts_links: bool
    source_kind: SourceKind = SourceKind.FETCHED_TEXT
    page_count: int = 0
    used_fallback: bool = False
    fallback_reason: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


@dataclass(frozen=True)
class ParseAttempt:
    """一次解析尝试的结果，包含失败原因 —— 用于决定是否 fallback。"""

    parser: str
    succeeded: bool
    document: ParsedDocument | None = None
    reason: str = ""


class DocumentParser(ABC):
    """文档解析器契约。"""

    name: str = "abstract"
    #: 本解析器产出的证据来源类型。
    source_kind: SourceKind = SourceKind.FETCHED_TEXT
    #: 本解析器抽不抽链接。**每个实现都必须在自己的类体里重新声明一次** ——
    #: 继承来的缺省值与「作者没想到这件事」在属性读取上完全一样，而两者的差别是
    #: 「这份文档没有链接」与「这份文档没被看过链接」。执行器在
    #: ``tests/test_parsers_report_links.py``：它遍历全部子类，缺声明的会被点名。
    #: 这个值由每个 ``parse`` 逐字写进 ``ParsedDocument.extracts_links``（写法就是
    #: ``extracts_links=self.extracts_links``），因为读文档的人手里没有解析器。
    extracts_links: bool = False

    @abstractmethod
    def can_parse(self, path: Path, *, content_type: str = "") -> bool:
        """快速判断能否处理该文件。不应读入整个文件。"""

    @abstractmethod
    def parse(self, path: Path, *, content_type: str = "") -> ParsedDocument:
        """解析文档。

        Raises:
            ParseError: 能处理但解析失败或产出为空。
            ParserUnavailableError: 依赖未安装。
        """


__all__ = [
    "DocumentLink",
    "DocumentParser",
    "PageCopier",
    "ParseAttempt",
    "ParsedDocument",
]

"""§5 的「evidence/result ingestion」这一格：抓到的材料变成一行**可被引用**的证据。

## 这一格在 ``MCP_SERVICE.md`` 的管道图上是写着的

    -> MarketOps research tools
       Serper | HTTP/PDF + Crawl4AI | Jev + Laya
    -> evidence/result ingestion          ← 这里
    -> persistent dossier, report and integration receipts

它此前是空的：``fetch_document`` / ``crawl_site`` 落盘了镜像字节、返回了
``body_sha256``，而证据库里一行都没有（SQLite ``evidence = 0``）。后果不是
「少了一条记录」—— 是 worker 手上**没有任何可以引用的 id**。``submit_findings``
的 ``evidence_refs`` 必须非空（``domain/findings.py``），它一条也填不出来，于是
正确地以 ``blocked`` 收场，而不是伪造一个引用。这一格补上之后，抓取这条路才
第一次有了「材料 → 引用」的闭环。

## 可引用的单位是**已取得的文档**，不是「支撑某字段的字面片段」

``Evidence.field`` 的缺省是 ``None``，而 ``ContactField`` 的 docstring 写的是
「**Contact 上**可以被证据支持的最小字段集」—— 这个 ``None`` 槽位本来就是给
非 Contact 的证据留的，不是这里新开的口子。``page_locator`` 同理（模型里举的
例子是 ``"page 3"`` / ``"block 12"``），而 §3 那行 ``get_evidence`` 的输入形状
正是「evidence id + **bounded locator**」。

于是文档级那一行是：``source_url`` / ``content_sha256`` 指向镜像字节，
``literal_value`` 是解析出来的正文，``field`` 为 ``None``。

## ``literal_value`` 为什么是正文本身

``Evidence`` 的不变量是「必须是来源文本里的**字面**片段」。正文是**唯一**一个
不需要任何约定就能满足它的取值 —— 挑「标题」或「第一段」都要先立一条本仓自己
的规矩，而那条规矩在源文档里没有对应物，于是它是本仓编出来的，不是读出来的。
代价是这一行可能很大，所以有界截断，而**截断写在 ``page_locator`` 里**。

## 这一行**不是** ContactRoute 的依据

``binding_eligible`` 说的是「来源层级够格」（抓来的 HTML 本来就在
``BINDING_ELIGIBLE_SOURCES`` 里），不是「这一行被用来绑过谁」。绑定的判据仍然
只在 ``domain/bind.py`` 里，而它只看 ``bind_from_text`` 自己抽出来的那些行。
两者的分界是 ``field``：绑定用的行一定有字段，文档级的行一定没有 ——
``get_account`` 的 ``evidence_refs`` 里两者并列，区分靠的就是它。
"""

from __future__ import annotations

from datetime import datetime

from ..domain.enums import SourceKind
from ..domain.evidence import Evidence, evidence_id
from ..domain.extract import page_publication_time
from ..ports.documents import ParsedDocument

__all__ = [
    "MAX_LITERAL_CHARS",
    "MAX_TITLE_CHARS",
    "TITLE_LOCATOR",
    "WHOLE_DOCUMENT_LOCATOR",
    "document_evidence",
    "title_evidence",
]

#: 标题那一行的位置描述。它是 §3 ``get_evidence`` 那个「bounded locator」的取值
#: —— 拿到一行证据的人要能说清这一行指着材料的哪一处，而「这一页自己写下的
#: 名字」是一个说得清的位置。
TITLE_LOCATOR = "document-title"

#: 标题这一格最多引用多少字符。**超出的部分不删**，只是不进这一行。
#:
#: 挑这个界不是省空间：一个指称对象不是一份文档，三百字符以上的那一格已经不是
#: 标题了 —— 它是解析出了别的东西（一封被塞进 ``<title>`` 的错误页正文）。
#: 截断的事实写在 ``page_locator`` 里，与正文那一行同一条纪律：**截断要被记录**。
MAX_TITLE_CHARS = 300

#: 一行文档级证据最多引用多少字符的正文。**超出的部分不删**，只是不进这一行 ——
#: 原文仍然在 ``content_sha256`` 指向的镜像字节里，而这一步是解析器输出的上界，
#: 不是新的抓取。截断的事实写进 ``page_locator``（见下面那一处）。
#:
#: ## 这是一个**存储**上界，不是回执上界
#:
#: 它原来是 4000，而那个数挑的理由是「一次 ``get_evidence`` 的载荷要有上界」。
#: 那个理由是对的，挑的**层**是错的：这两件事的读者不同。回执是给调用方读的，
#: 所以它的界该在读那一侧（``adapters/mcp_server/tools.py`` 的
#: ``_evidence_item``，那里已经对 ``context`` 做了同一件事）；而这一格是
#: **材料本身** —— 绑定层要从它里面找姓名、角色与邮箱，而那些东西常常在一页的
#: 后半段。
#:
#: 4000 的实际后果很安静：``submit_findings`` 那一侧能拿到的材料只有这一格，
#: 于是第 4000 字符之后的联系人**一条都绑不出来**，而报告上只是「这轮的路线
#: 少了一些」。这是「读侧有界、截断可见」这条样板被用错层之后的样子。
#:
#: 如今的取值只防一件真的事：一份病态的页面（解析器把整个站点吐成一段文本）
#: 不该把一行证据撑到无法处理。一页正文通常 5–50 KB。
MAX_LITERAL_CHARS = 200_000

#: 没截断时这一行的位置描述。整篇正文都是它的字面，所以位置就是「整篇」——
#: 编一个 ``"block 0"`` 会让读者以为这一行只覆盖正文的一段。
WHOLE_DOCUMENT_LOCATOR = "whole-document"


def document_evidence(
    *,
    workspace: str,
    account_id: str,
    source_url: str,
    document: ParsedDocument,
    fetched_at: datetime,
    http_status: int | None = None,
    tier: str = "",
) -> Evidence | None:
    """一份解析好的文档 → 一行可被引用的证据。**没有可引用的正文时返回 ``None``。**

    Args:
        workspace: 这一行的授权血统（本部署的 Scene，``Settings.profile``）。
            **必填、无默认值** —— 一个「缺省 = 当前 Scene」的默认值会把「没写
            血统」变成一次不留痕的放行。这一层没有第二个知道答案的地方：它拿到
            的只有一份解析结果，而解析结果**不是**一条血统声明。值由调用方从
            配置里取，见 ``application/discovery_run.py``（``bundle.profile_id``）
            与 ``application/pipeline.py``（``settings.profile``）。
            **MCP 那一侧的 ``_ingest`` 还没有传它** —— 那处调用点在本片的边界之外
            （``adapters/mcp_server/**`` 不动），它是现在唯一还在按旧签名调用这里
            的地方。
        account_id: 账号键。**必填且必须已经是规范 id** —— ``Evidence`` 自己的
            校验器会再校验一次，但那时错误是一句 ``ValidationError``，而这里
            能说清「是谁传进来的」。
        source_url: 实际取回的那一个 URL（``final_url``），不是请求的那一个：
            重定向之后两者不同，而证据要指向**真的读到内容的那一页**。
        document: 解析结果。正文、摘要、来源类型、解析器名、内容散列**全部**
            取自它 —— 不分成几个参数传，因为那样这个函数就能收到「A 文档的
            正文配 B 文档的散列」这种组合，而那种行在报告里完全正常。
        fetched_at: 取回时刻。**由调用方给**，因为它已经知道这个值（回执上
            就有），而在这里再取一次时钟会让两个字段差出几毫秒。
        http_status: HTTP 状态码。浏览器取回的那一次没有这个值。
        tier: ``http`` / ``browser``。同 ``Evidence.tier``。

    Returns:
        一行 ``Evidence``，或 ``None``。

    ``None`` 的**唯一**含义是「这份文档没有可引用的正文」（空白或空）。它是一个
    结果而不是异常：调用方要把它变成回执里的一句话（「这一页没有落成证据」），
    而抛出去之后能写下那句话的地方就被夹在 ``except`` 里，更常见的写法是把它
    并进那个接住一切的兜底 —— 于是「这一页是空的」与「这一格坏了」同形。
    """
    literal = document.text.strip()
    if not literal:
        return None
    # 这一页自己声明的发布时刻。抽的是 ``document.text``（与 ``literal_value``
    # 同一个输入），判据只有一份 —— ``domain/extract.py`` 的
    # :func:`~marketops.domain.extract.page_publication_time`。**这一行与
    # ``domain/bind.py`` 造出来的那些行因此说的是同一件事**：同一个 ``source_url``
    # 下，绑定用的证据与文档级的证据不会一个写着发布时刻、另一个写着 ``None``。
    published_at = page_publication_time(document.text)
    truncated = len(literal) > MAX_LITERAL_CHARS
    if truncated:
        # ``rstrip`` 不是洁癖：在空格处切开会留一个尾空白，而模型层的校验会
        # 因为那一格拒绝整条 —— 报出来的是一句「literal_value 首尾不得有空白」，
        # 看起来像数据坏了。
        literal = literal[:MAX_LITERAL_CHARS].rstrip()
    if not literal:
        return None

    return Evidence(
        workspace=workspace,
        evidence_id=evidence_id(
            account_id=account_id,
            source_url=source_url,
            field=None,
            literal_value=literal,
            digest=document.sha256,
        ),
        account_id=account_id,
        source_url=source_url,
        source_kind=document.source_kind or SourceKind.FETCHED_TEXT,
        literal_value=literal,
        content_sha256=document.sha256,
        fetched_at=fetched_at,
        source_published_at=published_at,
        field=None,
        # 整篇文档的那一行**没有**周围文本。留空是诚实的：往里填一句解释就是把
        # 「供人工复核的原文」这一格当成备注栏用，读的人不会再信它。
        context="",
        page_locator=(
            f"{WHOLE_DOCUMENT_LOCATOR} (chars 1..{len(literal)} of "
            f"{len(document.text.strip())})"
            if truncated
            else WHOLE_DOCUMENT_LOCATOR
        ),
        parser=document.parser,
        http_status=http_status,
        tier=tier,
    )


def title_evidence(
    *,
    workspace: str,
    account_id: str,
    source_url: str,
    document: ParsedDocument,
    fetched_at: datetime,
    http_status: int | None = None,
    tier: str = "",
) -> Evidence | None:
    """取回的材料**自己写下的标题** → 一行可被引用的证据。没有标题时返回 ``None``。

    ## 这一行与上面那一条的关系：它不是「同一件事的另一种写法」

    ``document_evidence`` 那一段里写着「挑标题当 ``literal_value`` 要先立一条本仓
    自己的规矩，而那条规矩在源文档里没有对应物」。那句话针对的是**整篇文档那一行**
    的字面，而它是对的。这一行支持的不是「整篇文档」，是**材料给自己写的名字**，
    而它的对应物在源文档里**是有的**：HTML 的 ``<title>``。规矩因此是从源格式里
    读出来的，不是本仓编的 —— 恰好就是那一段要求的判据。

    两行都要有，而不是把标题塞进正文那一行的 ``context`` 里：正文那一行会被
    :data:`MAX_LITERAL_CHARS` 截断，截断之后标题可能**根本不在**它引用的那段
    字面里，而「这条指称对象是从哪读来的」就再也验不了了。

    ## 边界（写给用它的人）

    这一行证明的是「这份材料写了这个名字」。它**不**证明这个名字是责任实体、
    也不证明它是这块场地的名字 —— 「这一页说它自己叫什么」与「这个组织叫什么」
    是两件事，把它们合成一件的后果写在 ``application/discovery_run.py`` 的模块
    docstring 里。

    ## 与正文那一行的 id 可能相撞

    ``evidence_id`` 是内容寻址的，而 ``field`` 两行都是 ``None``。一页的正文
    逐字等于它的标题时（``<html><head><title>X</title></head></html>`` 这种空壳，
    真的存在），两行的 id 会相同而 ``page_locator`` 不同 —— 那时
    ``EvidenceStore.put_evidence`` 会**正确地**拒绝（同一个 id 配两份内容）。
    调用方要自己处置这次相撞（只写一行），判据在
    ``application/discovery_run.py``；这里不替它决定，因为「哪一行留下」是调用方
    比这一层更清楚的事。
    """
    title = document.title.strip()
    if not title:
        return None
    truncated = len(title) > MAX_TITLE_CHARS
    if truncated:
        title = title[:MAX_TITLE_CHARS].rstrip()
    if not title:
        return None

    return Evidence(
        workspace=workspace,
        evidence_id=evidence_id(
            account_id=account_id,
            source_url=source_url,
            field=None,
            literal_value=title,
            digest=document.sha256,
        ),
        account_id=account_id,
        source_url=source_url,
        source_kind=document.source_kind or SourceKind.FETCHED_TEXT,
        literal_value=title,
        content_sha256=document.sha256,
        fetched_at=fetched_at,
        # 标题是这份材料给自己写的名字，与正文那一行**同属一页**，所以发布时刻
        # 也走同一个判据、得到同一个值。
        source_published_at=page_publication_time(document.text),
        field=None,
        context="",
        page_locator=(
            f"{TITLE_LOCATOR} (chars 1..{len(title)} of {len(document.title.strip())})"
            if truncated
            else TITLE_LOCATOR
        ),
        parser=document.parser,
        http_status=http_status,
        tier=tier,
    )

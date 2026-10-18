"""页面镜像的**位置约定**，以及按它读回字节。

## 为什么这是一个模块

这棵树的名字此前在两处各写了一遍（``http.py`` 与 ``crawl4ai_fetcher.py`` 的
``_persist``），而且**没有任何一处能读回它**。想读回就得写第三遍 —— 三遍里
只要有一遍漂了，读到的就是别人的字节：不报错、不空手，只是拿错材料。所以约定
只有一个定义处，两个写入方与读回方都从这里取。

``docs/DATA_MODEL.md`` 写着证据的 ``content_sha256``「指向镜像文件的具体字节」，
``ParsedDocument.sha256`` 写着它「让证据可以回溯到具体的镜像文件」—— 那两句话
在有一个能读回的实现之前是承诺，不是描述。这个模块是那个实现。

## 名字按 URL 取，字节按内容校验

文件名是 ``sha256_text(url)[:16]``，**不是**内容的散列 —— ``http.py`` 的模块
docstring 曾经把这一点写反了，还带一句由此推出的「不同 URL 内容相同不会产生
重复文件」（按 URL 命名时那句是错的）。这里记下来不是为了争一句措辞：**读回方
按 URL 前缀找文件**，命名规则一旦换成内容的散列，读回就只能全目录扫哈希。

分工是刻意的，出错时才看得出来：

* **名字负责定位** —— 同一个 URL 重复抓取落回同一个文件，不覆盖也不重复写。
* **字节负责校验** —— ``content_sha256`` 是那次抓取真正拿到的字节的散列，
  读回时重算一遍，不符就不认这条。

于是两种故障不会同形：「名字对不上」是这台机器没有那次抓取的字节；「名字对上
而字节不符」是那个 URL 的镜像被换过、或者这条证据来自另一次抓取。前者的处置是
换部署，后者是**证据链的证伪信号** —— 把它们合成一句「材料没找到」，会把一条
该被追查的事故注销掉。

## 同前缀不止一个文件

直连 tier 落 ``<hash16>.html``，浏览器 tier 落 ``<hash16>.rendered.html``：
同一次抓取的两个 tier **共享前缀**。这不是疏漏，也不该在名字上区分对待 ——
读回是在候选里按字节挑，不是在名字上做文章。

## 写入判据 :func:`store_page_bytes` 也只有一个定义处

名字算得对还不够：**同一个名字上第二次写什么**，是两个写入方曾经各自回答过一遍的
问题，而两个答案在浏览器那一侧曾经不同（无条件覆盖）。覆盖的后果不是「旧文件被改」，
而是上一条引用这个 URL 的证据从此读不回自己的字节 —— 读回方按 ``content_sha256``
在前缀候选里挑，挑不中报「散列不符」，而那一条信号**是我们自己的写入行为制造出来的**，
它本来该只在「这个世界真的变了」时出现。

所以写入那段判据（文件不存在就写、字节相同就不重复写、字节不同落到
:func:`page_version_path`）收在 :func:`store_page_bytes` 一处，两个抓取器与
:func:`copy_page`（把一次取回的字节落进另一个主体的目录）都调它。

第三类写入方是 :func:`copy_page`。它存在的理由写在 ``application/reuse.py``：同一
工作区里两个主体抓到同一个 URL 时只取一次，而**省下的是一次取回，不是一份磁盘** ——
每个主体留自己那一份，于是「不串账号」在盘上就是两棵各归各的目录，而一行证据照旧
能走回它自己的字节。
"""

from __future__ import annotations

import codecs
import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..domain.util import FILENAME_ID_MAX_LENGTH, sha256_text, slugify
from ..ports.documents import PageCopier
from .hashing import sha256_file

__all__ = [
    "PAGE_MIRROR_SUBDIR",
    "RENDERED_SUFFIX",
    "StoredPage",
    "account_page_dir",
    "as_text",
    "copy_page",
    "page_copier",
    "page_path",
    "page_prefix",
    "page_version_path",
    "read_stored_page",
    "store_page_bytes",
]

#: ``data_root/mirrors`` 之下页面镜像的那一层。
PAGE_MIRROR_SUBDIR = "pages"

#: 浏览器 tier 的产物后缀。它与直连 tier 共享**同一个前缀** —— 同一次抓取的两个
#: tier，读回时按字节挑，不在名字上分家。
RENDERED_SUFFIX = ".rendered.html"


def account_page_dir(settings: Settings, scope: str) -> Path:
    """某个主体（账号，或一轮发现运行）的页面镜像目录。

    那一层是分片的，用的是与 ``<account_id>.jsonl`` **同一个界**
    （``FILENAME_ID_MAX_LENGTH``）。两个界曾经差 20，于是「按 40 截断落盘、按 60
    读」在纸面上看不出来。

    参数叫 ``scope`` 而不是 ``account_id``：它的取值来自 ``FetchRequest.scope``，
    而那一个在发现期收下的是一轮运行的名字。理由写在
    ``ports/acquisition.py::FetchRequest`` 上。

    这个函数名本身**不改**：调用方要的是「按目录找回去」，而历史上那棵目录树
    就是按账号建的。名字指的是这棵树，不是入参。
    """
    return (
        settings.mirrors_dir
        / PAGE_MIRROR_SUBDIR
        / slugify(scope, max_length=FILENAME_ID_MAX_LENGTH)
    )


def page_prefix(url: str) -> str:
    """文件名里承担身份的那一段：URL 的散列前缀。"""
    return sha256_text(url)[:16]


def page_path(settings: Settings, *, scope: str, url: str, extension: str) -> Path:
    """一次抓取的镜像落在哪里。``extension`` 由调用方按内容类型给。

    ``scope`` 逐字来自 ``FetchRequest.scope``：抓取记在谁名下，镜像就落进谁的
    目录 —— 两处各算一遍的那一天，一条证据会归到一个没有那次抓取的主体上。
    """
    if not extension.startswith("."):
        raise ValueError(f"镜像扩展名必须以 '.' 开头，实际拿到 {extension!r}")
    return account_page_dir(settings, scope) / f"{page_prefix(url)}{extension}"


def page_version_path(
    settings: Settings, *, scope: str, url: str, bytes_sha256: str, extension: str
) -> Path:
    """**同一个 URL 换了内容之后**，那一次抓取的字节落在哪里。

    名字仍然以 :func:`page_prefix` 开头 —— :func:`read_stored_page` 按那个前缀
    找候选，再按 ``content_sha256`` 在里面挑，所以换一个后缀不必换读回方。
    这不是新机制，是本模块 docstring 那一节的直接推论：「同前缀不止一个文件」
    早就被设计成常态（两个 tier 就是这么共存的），第二次抓取只是第三类候选。

    参数是**原始字节**的散列（与 ``Evidence.content_sha256`` 同一个量），
    **不是** ``FetchResult.body_sha256`` —— 那一个是解码后文本的散列，两个都会
    用到，混起来会让名字里那一段看起来像在读回时要校验的东西。它取前 16 位进
    名字，于是**同一次抓取重复落盘落在同一个文件上**（名字是幂等的），而不同
    内容不会互相覆盖。名字里那一段**不校验**：校验在 :func:`read_stored_page`
    里按整份字节做，这里的唯一作用是让两次不同的抓取不至于抢一个文件名。
    """
    if not extension.startswith("."):
        raise ValueError(f"镜像扩展名必须以 '.' 开头，实际拿到 {extension!r}")
    return (
        account_page_dir(settings, scope)
        / f"{page_prefix(url)}.{bytes_sha256[:16]}{extension}"
    )


def store_page_bytes(
    settings: Settings,
    *,
    scope: str,
    url: str,
    payload: bytes,
    extension: str,
) -> Path:
    """把**已经拿到的**字节按本模块的约定落盘，返回装着它们的那个文件。

    调用方拿到的落点与同一行 ``FetchResult.body_sha256`` 必须描述**同一批字节**。
    只写「文件不存在才写」的后果：同一个 URL 第二次抓到**不同内容**时，新字节没有
    落盘，而 ``body_sha256`` 已经按新文本算过了 —— 读回方按 ``content_sha256`` 在
    前缀候选里挑，一个都挑不中，或者更糟：它挑中了旧文件，解析出旧内容，于是复核
    判定「没变」。那正是这一层最不该犯的错：**把一次真实的变更读成「什么都没发生」，
    而且不报错。**

    判据是**字节**而不是「文件在不在」：同 URL 重复写到同样的字节仍然不重复写
    （名字按 URL 取，这是本模块 docstring 里那条分工），内容变了才落到
    :func:`page_version_path` 给的第二个文件上。

    ``extension`` 由调用方按内容类型给（直连那一侧是
    ``http.py::_extension_for``，浏览器那一侧是 :data:`RENDERED_SUFFIX`，
    :func:`copy_page` 从源文件名里取回它）。
    """
    path = page_path(settings, scope=scope, url=url, extension=extension)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(payload)
        return path
    if path.read_bytes() == payload:
        return path
    versioned = page_version_path(
        settings,
        scope=scope,
        url=url,
        bytes_sha256=hashlib.sha256(payload).hexdigest(),
        extension=extension,
    )
    if not versioned.exists():
        versioned.write_bytes(payload)
    return versioned


def copy_page(settings: Settings, *, source: Path, scope: str, url: str) -> Path:
    """把 ``source`` 的字节落到 ``scope`` 名下、``url`` 的那个位置上。

    ## 为什么不是「让读回方去别的账号目录里找」

    一行证据走回它的材料，靠的是 ``(account_id, source_url, content_sha256)`` 三格
    （见 :func:`read_stored_page`）。让读回方在别人的目录里找，等于给这一层加一条
    **跨主体读** —— 而「不串账号」正是这一次共享要保住的东西。落一份自己的，
    读回路径一个字节都不用改。

    它省下的也确实是那一次取回：磁盘上多一份是这一次共享**没有**省的东西，而两者
    本来就不是一件事。

    ## 扩展名从名字里取回，不从调用方抄一遍

    源文件名是 ``<prefix16><扩展名>``（扩展名可能带版本段，见
    :func:`page_version_path`），所以 :func:`page_prefix` 之后的那一段就是它。
    调用方要想自己给一个扩展名，就得知道「这次取回是哪个 tier、什么内容类型」——
    那是抓取器才知道的事，而台账只知道「这两个主体要的是同一个 URL 的字节」。

    前缀对不上就抛：那说明要复制的那份字节不在它与这个 URL 配得上的名字下，
    复制过去会让一条证据指向一份不属于它的材料。
    """
    prefix = page_prefix(url)
    if not source.name.startswith(prefix):
        raise ValueError(
            f"要复制的镜像文件名不以 {prefix} 开头（{source.name!r}）—— "
            "它不属于这个 URL，复制过去会让读回方按前缀找到一份别人的材料"
        )
    extension = source.name[len(prefix) :]
    if not extension.startswith("."):
        raise ValueError(
            f"{source.name!r} 在 {prefix} 之后没有扩展名（取到 {extension!r}）—— "
            "这个名字不是本模块的约定算出来的"
        )
    return store_page_bytes(
        settings,
        scope=scope,
        url=url,
        payload=source.read_bytes(),
        extension=extension,
    )


def page_copier(settings: Settings) -> PageCopier:
    """把 :func:`copy_page` 绑到一份 settings 上，供注入（见 :data:`PageCopier`）。"""

    def copy(source: Path, scope: str, url: str) -> Path:
        return copy_page(settings, source=source, scope=scope, url=url)

    return copy


@dataclass(frozen=True)
class StoredPage:
    """读回来的一份镜像字节。

    ``head`` 是**有界**的一段（调用方给的界），``byte_length`` 是产物的**真实**
    大小。两个分开：把有界的头当成产物大小，会让一份被截断的材料看起来正好就是
    这么长，而「这个产物是否完整」正是读者要判断的事。
    """

    path: Path
    byte_length: int
    head: bytes
    truncated: bool

    def relative_ref(self, settings: Settings) -> str:
        """相对 ``data_root`` 的引用，理由同 ``_relative_ref``：引擎落在哪是部署的
        实现细节，调用方要的是一个能回传的引用。"""
        try:
            return str(self.path.relative_to(settings.data_root))
        except ValueError:
            return str(self.path)


def as_text(blob: bytes, *, complete: bool = True) -> str | None:
    """是 UTF-8 文本就给文本，不是就给 ``None``。

    **不猜编码。** 猜错的代价不是乱码，是一次看起来成功的读取：调用方拿到一段
    替换字符，而没有任何字段说它坏了。

    ``complete=False`` 表示 ``blob`` 是某个更大产物的**有界头**。截断落在字符
    中间时，那段不完整的尾序列是**这次读取**的性质，不是产物的性质 —— 用严格
    解码判它，会把一份正常的 HTML 报成二进制。所以尾部宽容、正文照旧严格：
    增量解码器 ``final=False`` 只保留末尾那个可能不完整的序列，中间任何一个
    真错的字节仍然抛 ``UnicodeDecodeError``。
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        return decoder.decode(blob, final=complete)
    except UnicodeDecodeError:
        return None


def read_stored_page(
    settings: Settings,
    *,
    account_id: str,
    source_url: str,
    content_sha256: str,
    max_bytes: int,
) -> tuple[StoredPage | None, str]:
    """按约定找回某次抓取的字节，**逐字节校验**。

    三个输入全部来自一行 ``Evidence``（``account_id`` / ``source_url`` /
    ``content_sha256``）—— 这正是「一行证据足以走回它的材料」那句话的形状。

    Returns:
        ``(page, "")`` 找到了；``(None, reason)`` 没找到，``reason`` 写清是哪一种
        没找到。两种「没有」的处置相反，所以不合并成一句。
    """
    prefix = page_prefix(source_url)
    directory = account_page_dir(settings, account_id)
    shown = _shown(directory, settings)

    if not directory.is_dir():
        return None, (
            f"这台机器上没有这个账号的页面镜像目录（{shown}），"
            "所以那次抓取的字节不在本地。"
        )

    candidates = sorted(path for path in directory.glob(f"{prefix}*") if path.is_file())
    if not candidates:
        return None, (
            f"{shown} 里没有以 {prefix} 开头的文件 —— 这个 URL 抓过的话，"
            "镜像会落在那个前缀下。"
        )

    for path in candidates:
        if sha256_file(path) != content_sha256:
            continue
        size = path.stat().st_size
        with path.open("rb") as handle:
            head = handle.read(max_bytes)
        return (
            StoredPage(
                path=path,
                byte_length=size,
                head=head,
                truncated=size > len(head),
            ),
            "",
        )

    return None, (
        f"{shown} 里以 {prefix} 开头的 {len(candidates)} 个文件，字节散列与 "
        f"{content_sha256[:16]}… 都不符 —— 这个 URL 的镜像被换过，或者这条证据"
        "来自另一次抓取。"
    )


def _shown(path: Path, settings: Settings) -> str:
    """回执里出现的路径一律是**相对**的。

    把绝对路径写进模型要读的那段话，等于把部署的目录布局当成接口的一部分：
    换个 ``MARKETOPS_DATA_ROOT`` 就换一段话，而读到它的人没有任何办法判断
    哪一段才是真的。
    """
    try:
        return str(path.relative_to(settings.data_root))
    except ValueError:
        return path.name

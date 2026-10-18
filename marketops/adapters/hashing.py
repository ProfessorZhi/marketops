"""文件内容寻址。

它从 ``marketops/domain/util.py`` 搬出来，不是因为「放错了文件夹」，而是因为
它是一个**实现**：要 ``pathlib``，要打开文件。只要它和那组纯字符串助手住在
一起，领域层就无法被无磁盘地测试 —— 任何 import ``domain.util`` 的模块都会
连带拿到读文件的能力，而分层的意义正是在这里被抹掉的。

内容寻址本身是本仓证据链的地基：``Evidence.content_sha256`` 记的是**这个字节
序列**，因此同一条证据可以被重新取到、比对、发现被改写。它必须与「谁抓的、
什么时候抓的」无关 —— 那是 ``Evidence`` 另外几个字段的事。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: 分块大小。大 PDF 与浏览器镜像都可以到几百 MB，整读会占满内存。
_READ_CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    """文件内容的 sha256，分块读取以免大 PDF 占满内存。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["sha256_file"]

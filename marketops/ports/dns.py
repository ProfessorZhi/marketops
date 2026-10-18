"""名字 → 地址。抓取层在**发出请求之前**需要的那一次测量。

## 为什么它是一个端口，而不是 ``adapters/`` 里的一个私有函数

采集设计 §7 要求「DNS 解析与实际连接绑定，防只检输入后 DNS 或重定向改向」。
要证明这件事真的发生了，测试必须能造出「一个名字解析到 ``169.254.169.254``」
这种局面 —— 而那在真实 DNS 上**不可构造**。解析器不做成可注入的，这条判据就只能
靠一个被污染的解析器或一次真实的内网探测来测，前者不可靠，后者是设计明令禁止的
（§7：测试用隔离受控目标，不向真实内网或云 metadata 探测）。

## 这个名字空间的三条纪律

* **空元组不是一种返回值。** 解析不出东西就抛 :class:`DnsResolutionError`。
  返回 ``()`` 会让调用方把它当成「解析出零个地址」，而判据那一侧对此必须
  失败关闭 —— 一个「什么都没解析出来就放行」的实现，看起来与「解析成功」同形。
* **三种失败不是一件事。** ``NONEXISTENT`` 说的是**这个来源可能没了**，
  ``TIMEOUT`` / ``FAILURE`` 说的是**我们这次没问到**。前者往 ``SOURCE_GONE`` 走，
  后者往「有界退避后再试」走 —— 合成一条会让一个已经消失的域名被无限重试。
* **这是测量，不是判据。** 地址好不好由 ``security/network.py::check_resolved``
  判 —— 那是纯函数，可以穷举；本端口只负责把事实量回来。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

__all__ = ["DnsFailureKind", "DnsResolutionError", "HostResolver"]


class DnsFailureKind(str, Enum):
    """解析失败的三种方式。**闭集**，理由与 ``TargetRefusal`` 同：
    开放字符串的第一个拼错的名字会静静开出一条新类别。"""

    #: 这个名字不存在（NXDOMAIN / NODATA）。一次「来源可能没了」的观测。
    NONEXISTENT = "nonexistent"
    #: 这次没问到（超时 / 暂时性失败）。它是**可重试**的那一类。
    TIMEOUT = "timeout"
    #: 问到了别的错误（服务不可用、内存、系统错误）。
    FAILURE = "failure"


class DnsResolutionError(Exception):
    """解析不出地址。**不是** ``OSError`` 的别名 —— 调用方按 ``kind`` 分支。"""

    def __init__(self, host: str, kind: DnsFailureKind, detail: str = "") -> None:
        super().__init__(f"解析 {host!r} 失败（{kind.value}）：{detail or '没有更多细节'}")
        self.host = host
        self.kind = kind
        self.detail = detail


class HostResolver(ABC):
    """把主机名解析成地址。实现见 ``adapters/resolution.py``。"""

    @abstractmethod
    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        """``host`` 的地址，**有序**（先问到的在前）。

        要 ``port`` 是因为有些解析器按服务端口决定问什么；这里是系统解析器，
        端口只影响不到的地址族，但签名里留着它比让实现偷偷用 0 好。

        Raises:
            DnsResolutionError: 解析不出来。**空元组不是一种返回值。**
        """

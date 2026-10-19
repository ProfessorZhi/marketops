"""``SystemResolver`` —— 本进程默认的解析器（``socket.getaddrinfo``）。

它做三件事，每一件都有理由：

1. **只问 TCP 流的地址**（``type=SOCK_STREAM``）。不加这个约束时，
   ``getaddrinfo`` 会为同一个名字回一串 UDP/SOCK_RAW 的地址族组合，
   于是「这个名字解析出几个地址」这件事在一台机器上是 3 个、在另一台是 8 个
   —— 而判据要对**每一个**地址都点头（失败关闭），地址族噪声会把它变成一个
   取决于 libc 版本的判决。
2. **去重且保序**。同一个地址经不同地址族重复出现是常态；重复不改变判决，
   但会让「钉在第一个地址上」这件事的日志读起来像有多个出口。
3. **把 ``gaierror`` 折成三种**（``DnsFailureKind``）。``EAI_NONAME`` 与
   ``EAI_NODATA`` 是「这个名字不存在」，``EAI_AGAIN`` 是「这次没问到」，
   其余是「问到了别的错误」—— 三者在下游的处置完全不同（见 ``ports/dns.py``）。

## 它**不**做的事：超时

``socket.getaddrinfo`` 没有超时参数，它按 ``/etc/resolv.conf`` 的
``timeout``/``attempts`` 走。用线程或信号给它套一个超时，会造出一个「我们放弃了
但那次解析还在跑」的状态 —— 而调用方会把它读成一次 ``TIMEOUT``。所以这一格如实
不设：**本模块不承诺解析耗时上限**，那一刻的上限由系统解析器配置决定。

## IP 字面量也走这里

``getaddrinfo("93.184.216.34", 443)`` 返回它自己。这不浪费：``check_target``
对字面 IP 已经判过一次，而 ``check_resolved`` 会再判一次 —— 两次判的是同一件事，
但第二次判的是**真正会被连上去的那些地址**，这正是「绑定」要的。
"""

from __future__ import annotations

import socket

from ..ports.dns import DnsFailureKind, DnsResolutionError, HostResolver

__all__ = ["SystemResolver"]


def _kinds_for(errno: int) -> DnsFailureKind:
    """``gaierror.errno`` → 三种失败之一。

    ``EAI_NODATA`` 在有些平台上与 ``EAI_NONAME`` 同值，所以按元组判而不是按
    相等判 —— 两个名字指向同一个值时，任何一处按名字分支的写法都会漏掉另一个。
    """
    nonexistent = {getattr(socket, "EAI_NONAME", -2), getattr(socket, "EAI_NODATA", -5)}
    timeout = {getattr(socket, "EAI_AGAIN", -3)}
    if errno in nonexistent:
        return DnsFailureKind.NONEXISTENT
    if errno in timeout:
        return DnsFailureKind.TIMEOUT
    return DnsFailureKind.FAILURE


class SystemResolver(HostResolver):
    """系统解析器。"""

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise DnsResolutionError(host, _kinds_for(exc.errno or 0), str(exc)) from exc
        except OSError as exc:  # 例如解析器配置本身坏了
            raise DnsResolutionError(host, DnsFailureKind.FAILURE, str(exc)) from exc

        addresses: list[str] = []
        for info in infos:
            address = info[4][0]
            if address not in addresses:
                addresses.append(address)
        if not addresses:  # pragma: no cover - getaddrinfo 空结果在实践中不存在
            raise DnsResolutionError(host, DnsFailureKind.NONEXISTENT, "解析器没有返回任何地址")
        return tuple(addresses)

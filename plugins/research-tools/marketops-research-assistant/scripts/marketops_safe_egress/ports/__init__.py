"""端口那一层在这个切片里只有一样东西：**名字 → 地址**（``dns.py``）。

核心的 ``ports/__init__.py`` 是一张整棵树的接口目录（检索、采集、文档、存储、
额度、决策）。这个切片不装那六样中的任何一样，照抄那张目录只会让读的人去找
不存在的模块。

``HostResolver`` 与 ``DnsResolutionError`` 留在这里而不是搬进 ``adapters/``，
理由与核心那一侧逐字相同（``ports/dns.py`` 的模块 docstring）：**它是测量，
不是判据**。把它做成可注入的端口，测试才造得出「一个名字解析到
``169.254.169.254``」这种局面 —— 而那在真实 DNS 上不可构造。
"""

from __future__ import annotations

__all__: list[str] = []

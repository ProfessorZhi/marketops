"""MarketOps 受管出口层 —— 核心那六个模块的**逐字节切片**，只依赖标准库。

## 这个包是什么

它是 ``marketops`` 仓库里出口判据的一个发行形式。客户机上装的是这个插件包，
而不是整棵核心树，所以判据要么在这里有一份，要么整个浏览器/辅助取回路径在客户机
上根本跑不起来。三条路里只有一条是对的：

* **让插件 import 核心** —— 把几百个模块与十几个第三方依赖带进一个只需要四步
  判据的地方；
* **在插件里重写一遍判据** —— 两份实现会在某一天分叉，而分叉的那一份看起来
  完全正常，它只是少了一次拒绝；
* **同一份源码，逐字节复制** —— 就是这里。

每个文件都与核心里的同名模块逐字节相同，而出处（路径 + sha256）写在
``PROVENANCE.json`` 上，由 ``tools/sync_safe_egress_bundle.py --check`` 与
``tests/test_wave1_egress_bundle.py`` 两条判据守着。**不要在这里改代码**：
改核心，再重新生成。

## 四层 ``__init__.py`` 为什么是手写的

核心的那些 ``__init__.py`` 装着整个包的导言（讲的是 ``marketops`` 这棵树的取向、
分层与历史）。照抄进一个只有六个模块的切片里，是一篇指错地方的文档。所以判据落在
**实现文件**上：策略住在哪几个文件里，哪几个文件就被钉死。

## 一条承重的读法

这个包提供的是**判据与出口**，不是**可信内容**。``EgressProxy`` 让浏览器只有一条
出口，``GuardedHttpClient`` 让辅助取回每一跳都判且钉地址 —— 而两者都看不见
端到端 TLS 隧道里的正文。一次「出口放行」不是「这一份证明是真的」。

## 名字

顶层 import 名是 ``marketops_safe_egress``（不是 ``marketops``）：它与核心包**可以
同时装在一台机器上**而互不干扰，这一点是刻意的 —— 开发机上两者都在，而
``marketops`` 这个名字属于核心。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .adapters.egress_proxy import (
    EgressDecision,
    EgressGate,
    EgressProxy,
    EgressVerdict,
)
from .adapters.guarded_http import (
    BROWSER_EGRESS_ARGUMENTS,
    DEFAULT_LIMITS,
    EgressBudget,
    EgressBudgetExceeded,
    EgressLimits,
    EgressRefusal,
    GuardedFetch,
    GuardedHttpClient,
    GuardedResponse,
    browser_egress_arguments,
    browser_proxy_config,
    decision_ref,
)
from .adapters.resolution import SystemResolver
from .ports.dns import DnsFailureKind, DnsResolutionError, HostResolver
from .security.network import (
    ALLOWED_SCHEMES,
    DEFAULT_EGRESS_PORTS,
    MAX_REDIRECTS,
    RESERVED_HOSTS,
    RESERVED_HOST_SUFFIXES,
    TargetRefusal,
    TargetVerdict,
    check_port,
    check_resolved,
    check_target,
    is_loopback_host,
)
from .security.scope import (
    ScopeConfigError,
    ScopeRefusal,
    ScopeVerdict,
    check_domain,
    normalise_host,
    parse_domain_allowlist,
)

#: ``PROVENANCE.json`` 的 ``schema``。调用方拿它对自己手上的这一份与期望的那一份。
SLICE_SCHEMA = "marketops.safe-egress-slice.v1"

#: 这个发行形式的版本。**不跟核心包的版本走** —— 一次切片重新生成（比如多带进来
#: 一个模块）是一次发行事件，而核心的那次改动的版本号与它无关。
__version__ = "1.0.0"

__all__ = [
    "ALLOWED_SCHEMES",
    "BROWSER_EGRESS_ARGUMENTS",
    "DEFAULT_EGRESS_PORTS",
    "DEFAULT_LIMITS",
    "MAX_REDIRECTS",
    "RESERVED_HOSTS",
    "RESERVED_HOST_SUFFIXES",
    "SLICE_SCHEMA",
    "DnsFailureKind",
    "DnsResolutionError",
    "EgressBudget",
    "EgressBudgetExceeded",
    "EgressDecision",
    "EgressGate",
    "EgressLimits",
    "EgressProxy",
    "EgressRefusal",
    "EgressVerdict",
    "GuardedFetch",
    "GuardedHttpClient",
    "GuardedResponse",
    "HostResolver",
    "ScopeConfigError",
    "ScopeRefusal",
    "ScopeVerdict",
    "SystemResolver",
    "TargetRefusal",
    "TargetVerdict",
    "browser_egress_arguments",
    "browser_proxy_config",
    "check_domain",
    "check_port",
    "check_resolved",
    "check_target",
    "decision_ref",
    "is_loopback_host",
    "normalise_host",
    "parse_domain_allowlist",
    "slice_provenance",
]

_PROVENANCE = Path(__file__).resolve().parent / "PROVENANCE.json"


def slice_provenance() -> dict[str, Any]:
    """读回 ``PROVENANCE.json``。

    **读不出来时抛**，不返回一个空字典：一个空字典会让「出处在哪」这个问题得到
    一个看起来正常的答案（「没有文件」），而真正发生的事是这一份切片不完整。
    """
    return json.loads(_PROVENANCE.read_text(encoding="utf-8"))

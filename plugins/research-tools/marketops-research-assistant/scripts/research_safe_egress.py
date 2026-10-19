#!/usr/bin/env python3
"""**别名**：本模块只是 ``research_egress`` 的转发，没有自己的实现。

## 为什么有这一格

派发 prompt 里写的入口名是 ``research_safe_egress``，而协调目录的
``WORKER-DAG-AND-OWNERSHIP.md`` §3 把 W-A SECURITY 的独占路径写成
``scripts/research_egress.py``。**两个名字都出现过**，而 INTEGRATOR 手上拿到的
可能是其中任何一个。

比「让 INTEGRATOR 去猜」更好的做法是让两个名字都能 import，同时**明确哪一个是真的**：
正本是 :mod:`research_egress`，它装着全部行为、全部不变量与全部理由码；
本模块一行实现都没有 —— 它是一次 ``from … import`` 加一张 ``__all__``。

## 它的判据

``tests/test_wave1_egress_entry.py`` 判的是**同一性**：本模块与 ``research_egress``
里那些名字指向**同一个对象**（``is``，不是 ``==``）。一个在这里手写了第二份实现的
别名会在那一条上红 —— 而那是这一格唯一会有的失败方式。要退役它也很干净：
把两个名字都改掉、删掉本文件即可，没有任何行为住在里面。
"""

from __future__ import annotations

from research_egress import (  # noqa: F401 - 全部都是转发，一行实现都没有
    ALLOWED_DOMAINS_ENV,
    CONTRACT_EGRESS_REASONS,
    CONTRACT_FOLD,
    DEFAULT_CRAWL_SCOPE_DEPTH,
    DEFAULT_CRAWL_SCOPE_PAGES,
    READINESS_CAUSES,
    REASONS,
    SAFE_EGRESS_API_VERSION,
    CrawlScope,
    EgressDenied,
    SafeEgressSession,
    allowed_domains_from_env,
    contract_reason_code,
    crawl4ai_ready,
    local_crawl_ready,
    main,
    open_session,
    reason_code,
    safe_browser_egress,
)

__all__ = [
    "ALLOWED_DOMAINS_ENV",
    "CONTRACT_EGRESS_REASONS",
    "CONTRACT_FOLD",
    "DEFAULT_CRAWL_SCOPE_DEPTH",
    "DEFAULT_CRAWL_SCOPE_PAGES",
    "READINESS_CAUSES",
    "REASONS",
    "SAFE_EGRESS_API_VERSION",
    "CrawlScope",
    "EgressDenied",
    "SafeEgressSession",
    "allowed_domains_from_env",
    "contract_reason_code",
    "crawl4ai_ready",
    "local_crawl_ready",
    "main",
    "open_session",
    "reason_code",
    "safe_browser_egress",
]


if __name__ == "__main__":  # pragma: no cover - 与正本同一条命令
    raise SystemExit(main())

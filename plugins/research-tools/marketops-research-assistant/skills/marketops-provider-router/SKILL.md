---
name: marketops-provider-router
description: Choose and combine Serper, Crawl4AI, TinyFish and TypeSafe Jev for a research-stage data gap; inspect actual MCP/API capabilities, preserve budget and evidence lineage, and diagnose before switching.
---

# 四Provider路由

先读 [路由合同](../../references/PROVIDER_ROUTING.md) 与 [统一交接](../../references/DATA_HANDOFF.md)。输入：已批准Brief/Run、当前store、缺失事实、可用工具和费用范围。

1. 先查已有资产/证据和当前阶段，避免重复搜索。
2. 发现本宿主真实MCP catalog/SDK能力，分别记录配置、认证、真实调用状态；不要凭Skill假设API可用。
3. 未知URL→Serper/TinyFish search；有URL→Crawl4AI/TinyFish fetch；稳定批量→Crawl4AI；必须交互→获批TinyFish；已有证据的语义判断→Jev；计算/去重/硬规则→代码。
4. 选择后仅加载对应 `marketops-serper`、`marketops-crawl4ai`、`marketops-tinyfish` 或 `marketops-jev` Skill。不要一次加载四套全文。
5. 决策留短记录：目标缺口、为何选择、实际工具/版本、授权引用、材料类型、预期产物、操作id。返回后追加实际结果、用量/未知、写回与读回定位符。
6. 失败先定位层次；DNS迁移、解析、存储与WAF不是同类。不要把timeout自动解读为未计费，不用另一个工具绕同一访问墙。
7. 当前目标已完成/明确阻塞/额度耗尽时停止对应对象或方法，其他已授权独立对象可继续。

输出为真实来源/资料/判断的引用和持久化处置，不是口头说“工具跑了”。每次卡点、换路、成功改进由 `marketops-research-retrospective` 记录。此Skill不实现网关或硬权限。


## v0.3.0 双搜索

已接入本地 MarketOps Dual Search MCP 候选：`marketops_search` 指定 `serpapi` 或 `serper`。先调用 `marketops_search_capabilities` 做不联网检查，真实请求需要操作者密钥/网络开关/调用上限。失败后禁止静默换另一家，完整配置见 references/DUAL_SEARCH_SETUP.md。

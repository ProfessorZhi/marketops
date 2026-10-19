---
name: marketops-serpapi
description: Use SerpApi official multi-engine search and Google Maps place IDs as discovery-only leads, with explicit account, provider selection, provenance and no automatic paid fallback.
---

# MarketOps · SerpApi 专项搜索

SerpApi.com 与 Serper.dev 是两家独立厂商，API Key、结构和账单不可混用。SerpApi 官方托管 MCP：https://mcp.serpapi.com/mcp；官方源码：https://github.com/serpapi/serpapi-mcp；Key 申请：https://serpapi.com/manage-api-key。该开源的是 MCP Server 包装，而非 SerpApi 搜索服务。

首选能力场景：
- 多引擎网页结果、需要复杂 SERP 格式或其他搜索平台时；
- Google Maps 场地候选，保留 place_id、data_id、来源坐标、排名与访问时间；
- 使用同一研究样本与 Serper 做成本/有效候选率比较时。

在已授权的 MarketOps Lite Dual Search MCP 中明确选择 `provider="serpapi"` 与 `mode="web"|"maps"`；必须填 country、language、run_id、operation_id，不允许自动回落另一家。若是直连 SerpApi 官方 MCP，先列出实际工具 schema，按其引擎参数执行，并明确记录独立调用的资料没有自动经过 MarketOps 事件/业务写入通道。

每次调用只能生成来源候选，不直接产出已核实场地、组织责任、人名任职或邮件联系人。Maps Place ID 只是有帮助的供应商标识，不是 MarketOps 组织主键；官网证据与真实归属关系仍须单独获取。

出现 401/429、配额、无结果或前端页面阻断时，不擅自用 Serper 再付费搜索；记录失败原因或未知，评估是否需要另一家并单独确认授权。不得把真实 Key 写入 Skill、Prompt、Git、日志或 Excel。

路由合同：[PROVIDER_ROUTING](../../references/PROVIDER_ROUTING.md)。当前试用目录：[STORAGE_AND_TOPOLOGY](../../references/STORAGE_AND_TOPOLOGY.md)。 
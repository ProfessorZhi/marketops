# WorkBuddy 接入：MCP + Skill 连接器候选

> **更新（v0.2）**：统一 [MarketOps Research Assistant](../marketops-research-assistant/README.md) 可以生成 **skill-only** 连接器，和供应商 MCP 连接器分别安装；WorkBuddy 每个 MCP 连接器只能配置一个服务。本页保留 TinyFish/Crawl4AI 的独立 Provider 连接说明，并非统一插件的全部配置。

官方文档：https://open.workbuddy.cn/docs/connector 。WorkBuddy 支持 **MCP + Skill** 和 **CLI + Skill**；本目录的候选包采用远程 HTTPS MCP，不要求本机安装 Crawl4AI 浏览器。

## 准备

1. 在 WorkBuddy 里查看版本与已启用的 MCP/连接器，记录是否有权限安装自定义连接器。
2. 不要覆盖已有 MCP 配置；一个连接器只配置一个 MCP Server。
3. 首轮不要开启自动定时任务、充值、浏览器付费 Agent 或外联操作。

## TinyFish（优先）

候选目录：[workbuddy/tinyfish](../workbuddy/tinyfish/)。远程地址 `https://mcp.tinyfish.ai`，使用原生 OAuth，首次请求时用户自己授权。若客户端不支持打包导入，可在其支持的 MCP 配置 UI 中手动添加同一服务地址。该候选包包含禁用付费 Agent 和监控入口的客户端过滤列表，真实工具名与过滤结果必须在客户端再验收。

## Crawl4AI

候选目录：[workbuddy/crawl4ai](../workbuddy/crawl4ai/)。需要从 https://api.crawl4ai.com/ 自行申请 API Key，使用 WorkBuddy **用户自填 Token** 模式（最低 WorkBuddy 4.23.0）；`token-schema.json` 收集密钥，`mcp.json` 通过 `${CRAWL4AI_API_KEY}` 变量发送 Bearer Token。实际 Key 不保存在提交文件中。

## Serper / Jev

Serper 无已验证的官方 MCP 连接器，Jev 的官方项目首先提供 Skill/SDK。这里不编造两个“官方 WorkBuddy 插件”。用户可以阅读 [Serper](../providers/serper.md) 和 [Jev](../providers/jev.md) 的申请指南，使用手工 API 或等待受管 MarketOps MCP 整合。

## WorkBuddy 版本事项

- 连接器结构应包含 `connector-meta.json`、`mcp.json`、`icon.svg` 和可选 `skills/*/SKILL.md`。
- `auth_mode: token` 和 `token-schema.json` 需要至少 4.23.0；`name_zh`、`examples_zh` 等字段需要 4.24.0。本候选包声明 4.24.0。
- `disabledTools` 只限制 WorkBuddy 向 Agent 展示的工具，是客户端级保护，不代表外部 API 永远不会收费。

## 真实验收

在真实 WorkBuddy 客户端：导入 → 认证 → 列出可见工具 → Search/Fetch 小规模任务 → 对照来源核验 → 清理测试数据/断开连接测试。每一步记录 `PASS`、`BLOCKED` 等状态。不以 JSON 可解析代替兼容性结论。


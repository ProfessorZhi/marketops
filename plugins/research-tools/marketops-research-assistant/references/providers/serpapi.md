# SerpApi.com — 官方多搜索引擎 API + 官方开源 MCP

> **重要辨析：SerpApi.com 不是 Serper.dev。** SerpApi 的账号/API Key 页面为 https://serpapi.com/manage-api-key ；当前 MarketOps 正式 Serper Adapter 使用 https://google.serper.dev/search ，不能直接拿 SerpApi 的 Key 配进去。此文件是可选替代方案的官方资源与客户端试用指导，**并不表示 MarketOps 已接好 SerpApi Adapter**。

## 官方入口与源码

- API Key：https://serpapi.com/manage-api-key （登录后可见；不要在聊天、Git、截图透露 Key）
- 产品及搜索 API：https://serpapi.com/ 和 https://serpapi.com/search-api
- **官方 SerpApi MCP 使用指南**：https://serpapi.com/integrations/mcp
- **官方开源 MCP 代码**：https://github.com/serpapi/serpapi-mcp （MIT）。可自部署**MCP 网关进程**；底层 Google/Bing/Maps 等搜索仍由 SerpApi 托管并计费，并非可以离线部署搜索引擎。
- 当前套餐：https://serpapi.com/pricing 。常见免费层 250 searches/month，Starter $25/month（1,000 searches）。以实际账户/套餐和请求类型为准。

## 推荐：Codex 官方 HTTP MCP 配置

在**使用者自己的** Codex 环境中安全设置 `SERPAPI_API_KEY` 后：

```bash
codex mcp add serpapi --url https://mcp.serpapi.com/mcp --bearer-token-env-var SERPAPI_API_KEY
codex mcp list
```

这是 SerpApi **官方**的 Codex 命令。首次请求之前先完成凭据与额度确认；`mcp list` 表示配置注册，**不等于** API 请求或最终调研验收已通过。服务端工具 schema 实际以 `tools/list` 为准。

避免直接把 Key 塞进 `https://mcp.serpapi.com/KEY/mcp` 这类 URL。官方虽然支持该形式，但 URL 易进入连接日志；支持时首选 `Authorization: Bearer`，通过环境变量或安全凭据配置，而不是硬编码 Key。

## WorkBuddy 与其他 MCP 客户端

可在支持的 WorkBuddy 版本中新增**单独的** HTTPS MCP 连接器：
- URL: `https://mcp.serpapi.com/mcp`
- Auth: `Authorization: Bearer <该客户自行配置的 SERPAPI_API_KEY>`
- 让 WorkBuddy 凭据表单或 OS Secret 机制持有真实 Key；**不要**提交实际 Token 到 connector-meta、mcp.json 或 Skill 文件。
- 本仓当前**还没有**正式验收的 SerpApi WorkBuddy 导入包。需测试工具列表、Headers/Token 注入、认证、费用、断开连接/卸载。

官方 MCP 包也可从上述 GitHub 仓库自托管（需要匹配 Python/uv 的官方发行环境和受控运行权限），但**仍需** SerpApi 云端 API Key。

## 与 Serper 的选择

|  | SerpApi.com | Serper.dev |
|---|---|---|
| 产品重点 | Google/Maps 及 Bing、Yahoo、DuckDuckGo 等多引擎与更深 SERP 结构 | Google Search/Maps/Places 等，偏高量低成本 |
| 厂商官方 MCP | **有**，托管 + MIT 开源服务代码 | 未确认厂商官方通用 MCP；常见社区实现并非官方 |
| 示例免费额度 | 250 searches/month | 一次性试用 2,500 queries |
| 官方入门价 | $25/month / 1,000 searches | $50 / 50,000 credits（6 个月有效） |
| 当前 MarketOps 代码 | **没有 SerpApi Adapter** | **已实现** `SerperSearchProvider` |

SerpApi 的 Google Maps Search 返回 `place_id`、`data_id`、`gps_coordinates`、可分页结构，对场地/地点候选可能更适合保留身份候选标识；**但 Maps 命中并不证明运营/采购身份**，仍需官网/可靠证据核验。实际查询召回、字段质量、费用和时延在 Member-A 目标市场上尚未 A/B 验证，不能宣称 SerpApi 全面更准确。

## 如果实际账户属于 SerpApi，如何修 MarketOps

当前仓库后端 `marketops/adapters/serper.py`、`SERPER_API_KEY`、`https://google.serper.dev/search` 是**另一个服务**。不要修改 URL 或改一个环境变量名就把它当作 SerpApi。应在 `SearchProvider` 抽象下新建 `SerpApiSearchProvider`，对接 `https://serpapi.com/search` 与 `api_key`，显式规范 `engine`、locale、地图 place ID、分页、错误和预算单位，然后按 Scene/部署选择 `serpapi` 或 `serper`，保留统一 SearchHit/SourceReceipt/Evidence 规则。任何正式切换需要客户明确选择和测试。

## 注意

`SERPAPI_API_KEY`（SerpApi）与 `SERPER_API_KEY`（Serper）不能互相替用。保存原始 JSON 仅证明搜索来源，搜索摘要不能直接晋升为场地和联系人证据。不启用自动外联/付费升级。

官方参考：
- https://serpapi.com/integrations/mcp
- https://github.com/serpapi/serpapi-mcp
- https://serpapi.com/maps-local-results
- https://serpapi.com/pricing
- https://serper.dev/

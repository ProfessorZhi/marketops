# 开发者示例 · 不为受管分发（developer example, NOT FOR MANAGED DISTRIBUTION）

**状态：** `DEVELOPER_ONLY` · 2026-10-17
**P1-02 标注：** 本目录**不为受管分发**（not for managed distribution）产物的一部分。

本目录把 WorkBuddy 直接接到 `https://api.crawl4ai.com/mcp`，密钥由用户自填进
宿主 Token 表单（`${CRAWL4AI_API_KEY}`），**不经过** MarketOps 唯一门面。
受管产品里不允许出现启用的直连旁路。

## 事实（可复核）

| 事实 | 怎么复核 |
| --- | --- |
| 标准安装器**不复制**本目录 | `setup_clients.py` 只复制 `marketops-research-assistant/` |
| 连接器生成器**不复制**本目录 | `build_client_bundles.py` 只从该插件目录取文件 |
| 受管的 Crawl4AI 走本地 SDK | 统一插件里的 `marketops_crawl`（本地模式无需 Cloud Key） |
| 上层包无受管入口指向它 | `client_install_probe.py::PACKAGED_VENDOR_MCP` 断言 |

## 工具面：正向显式白名单（P1-02）

`mcp.json` 用 `enabledTools` 列出**允许**的工具（`scrape`、`extract`），取代此前
按 `topup` / `recharge` / `spend_cap` / `report_issue` 排除的拒绝名单 —— 拒绝名单挡不住
上游明天新增的工具，允许清单挡得住。

## 仍未验证的事

- `enabledTools` 的真实过滤效果**未经真实客户端验收**；允许的工具名取自官方文档的
  `/scrape`、`/extract` 端点写法，**未与上游 MCP `tools/list` 逐项核对**。若名字不符，
  客户端应当**拒绝**而不是放行（fail-closed），这正是本改动的方向。
- Cloud 计费归属、Key 的作用域与轮换**未验证**；直连调用不进 MarketOps 账本与证据面。
- `token-schema.json` 的 Token 模式在 WorkBuddy 4.23.0+ 上的行为**未在设备上验收**。

## 删除纪律

不删，保留待复核；删除或改写需先说明开发对照价值。

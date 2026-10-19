# 开发者示例 · 不为受管分发（developer example, NOT FOR MANAGED DISTRIBUTION）

**状态：** `DEVELOPER_ONLY` · 2026-10-17
**P1-02 标注：** 本目录**不为受管分发**（not for managed distribution）产物的一部分。

本目录是一套**厂商直连**示例：它把宿主直接接到 `https://mcp.tinyfish.ai`，
不经过 MarketOps 的唯一门面。按 owner brief §2.5 与架构收敛决定 §3，受管产品里
**不允许**出现启用的直连旁路；本目录保留的理由是开发与对照，不是产品能力。

## 事实（可复核）

| 事实 | 怎么复核 |
| --- | --- |
| 标准安装器**不复制**本目录 | `setup_clients.py` 只 `shutil.copytree(SOURCE, ...)`，`SOURCE` = `marketops-research-assistant/` |
| 连接器生成器**不复制**本目录 | `build_client_bundles.py` 只从 `HERE`（同一个插件目录）取文件 |
| 本目录不是 v0.4 插件的一部分 | 它是 v0.1 候选，见 `plugins/research-tools/README.md` |
| 上层包没有任何受管入口指向它 | `client_install_probe.py` 的 `PACKAGED_VENDOR_MCP` 断言 |

## 工具面：正向显式白名单（P1-02）

`.mcp.json` 用 `enabledTools` 列出**允许**的工具（`search`、`fetch_content`），
不是用拒绝名单列出禁止的工具。方向是 fail-closed：上游明天新增的工具**默认被拒**，
直到有人在此复核后显式加入。

## 仍未验证的事（不要当成已验证）

- `enabledTools` 在本示例里的**实际过滤效果未经任何真实客户端验收**。字段名沿用本仓
  WorkBuddy 连接器的既有写法，**不代表 Codex 一定会遵守**。允许的工具名取自
  `providers/tinyfish.md` 的 `search` / `fetch_content`，没有从上游 `tools/list` 逐项核对过。
- 直连 OAuth、额度、计费归属全部**未验证**：本示例若被真实使用，费用与权限直接落在
  用户自己的厂商账号上，MarketOps 的预算/审计/证据边界**不覆盖**它。

## 删除纪律

按 W-G 的授权：**不删**，先保留待复核。谁要删除或改写本目录，需先说明它是否还有
开发对照价值；该决定不由本文件的作者单方面做出。

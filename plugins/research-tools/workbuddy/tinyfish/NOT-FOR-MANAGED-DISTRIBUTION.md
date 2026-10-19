# 开发者示例 · 不为受管分发（developer example, NOT FOR MANAGED DISTRIBUTION）

**状态：** `DEVELOPER_ONLY` · 2026-10-17
**P1-02 标注：** 本目录**不为受管分发**（not for managed distribution）产物的一部分。

本目录把 WorkBuddy 直接接到 `https://mcp.tinyfish.ai`，不经过 MarketOps 唯一门面。
按 owner brief §2.5 与架构收敛决定 §3，受管产品里不允许出现启用的直连旁路。

## 事实（可复核）

| 事实 | 怎么复核 |
| --- | --- |
| 标准安装器**不复制**本目录 | `setup_clients.py` 只复制 `marketops-research-assistant/` |
| 连接器生成器**不复制**本目录 | `build_client_bundles.py` 只从该插件目录取文件 |
| 本目录是历史 v0.1 候选 | `plugins/research-tools/README.md` 第 46 行 |
| 上层包无受管入口指向它 | `client_install_probe.py::PACKAGED_VENDOR_MCP` 断言 |

## 工具面：正向显式白名单（P1-02）

`mcp.json` 用 `enabledTools` 列出**允许**的工具（`search`、`fetch_content`）。
这是一份**允许清单**而不是拒绝清单：上游新增的工具默认被拒，直到在此复核后显式加入。

## 仍未验证的事

- `enabledTools` 的真实过滤效果**未经真实客户端验收**；允许的工具名取自
  `providers/tinyfish.md`，未与上游 `tools/list` 逐项核对。
- OAuth、额度、计费归属未验证；MarketOps 的预算/审计/证据边界不覆盖直连。
- 真实工具名与过滤结果必须在客户端再验收（见 `plugins/research-tools/clients/workbuddy.md`）。

## 删除纪律

不删，保留待复核；删除或改写需先说明开发对照价值。

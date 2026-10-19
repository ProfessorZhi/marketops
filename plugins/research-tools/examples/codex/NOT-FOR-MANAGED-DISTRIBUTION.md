# 开发者示例 · 不为受管分发（developer example, NOT FOR MANAGED DISTRIBUTION）

**状态：** `DEVELOPER_ONLY` · 2026-10-17
**P1-02 标注：** 本目录**不为受管分发**（not for managed distribution）产物的一部分。

本目录保存 **Codex 用户自建配置** 的片段：把 TinyFish / Crawl4AI 的**官方 MCP**
直接写进用户自己的 `~/.codex/config.toml`，不经过 MarketOps 唯一门面。受管产品里
不允许出现启用的直连旁路；本文件保留的理由是开发与对照（用户按官方文档自接时抄这里）。

## 事实（可复核）

| 事实 | 怎么复核 |
| --- | --- |
| 标准安装器**不复制**本目录 | `setup_clients.py` 只 `shutil.copytree(SOURCE, ...)`，`SOURCE` = `marketops-research-assistant/` |
| 连接器生成器**不复制**本目录 | `build_client_bundles.py` 只从该插件目录取文件 |
| 安装产物里没有 `config.toml.example` | 独立 QA 用例 `test_retained_direct_connect_examples_are_absent_from_install_and_bundle` |
| 受管插件自己的 Codex 面只有一个 MCP | `.codex-plugin/plugin.json` → `./.mcp.json` → 唯一的 `marketops-research` |

## 工具面：正向显式白名单（P1-02）

两份 `[mcp_servers.*]` 段落都只用 `enabled_tools` 列出**允许**的工具
（tinyfish: `search` / `fetch_content`；crawl4ai: `scrape` / `extract`），
不使用 `disabled_tools`。上游新增的工具默认被拒，直到在此复核后显式加入。

## 仍未验证的事

- 这些示例**未经真实 Codex 客户端验收**；允许的工具名取自官方文档与
  `providers/tinyfish.md`，**未与上游 MCP `tools/list` 逐项核对**。
- 直连 OAuth、额度、计费归属未验证；MarketOps 的预算/审计/证据边界不覆盖直连。

## 删除纪律

不删，保留待复核；删除或改写需先说明开发对照价值。

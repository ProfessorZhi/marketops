# Codex 接入：官方工具优先，逐个启用

> **更新（v0.2）**：建议优先使用 [统一 MarketOps Research Assistant](../marketops-research-assistant/README.md)。本页保留单个 Provider 的设置示例；`codex/marketops-research-starter/` 是之前的 v0.1 候选，不是现在的唯一规范入口。

目的：让用户自己的 Codex 先做半自动研究，不使用东京服务器上的开发 CC 身份，不部署 MarketOps 后端。

## 1. 准备

在用户本机确认 Codex 与 Node.js/npx 的版本。建议先备份现有 `~/.codex/config.toml` 并检查其中已有 MCP 项，不要覆盖其余服务器。

## 2. 最短路径：TinyFish 官方连接器

    npx @tiny-fish/cli connect codex --dry-run
    npx @tiny-fish/cli connect codex
    codex mcp list

官方安装器：https://github.com/tinyfish-io/tinyfish-cookbook/blob/main/INSTALL.md 。第一次真实使用前用户完成 OAuth；CLI 注册成功**不代表**认证与工具成功。

## 3. Crawl4AI（按需）

先从 https://api.crawl4ai.com/ 注册获得 Cloud API Key，再将用户自己的凭据放入受保护的 `CRAWL4AI_API_KEY` 环境变量（必须在启动 Codex 的进程中可见）。以下片段来自官方 Codex MCP 配置字段：

    [mcp_servers.crawl4ai]
    url = "https://api.crawl4ai.com/mcp"
    bearer_token_env_var = "CRAWL4AI_API_KEY"
    disabled_tools = ["topup", "recharge", "spend_cap", "report_issue"]

请参考 [配置候选](../examples/codex/config.toml.example)，将片段**合并**到自己现有的 TOML，不要整份覆盖。`disabled_tools` 是 Codex 的本地工具限制，不替代 Provider 级付费上限。Cloud MCP 具有收费操作，未经授权不能调用。

## 4. Serper 与 Jev

- Serper：官方提供 API；目前不能声称存在 Serper 官方 MCP。优先采用后续受管 MarketOps MCP，独立试验时人工使用官方 API。参见 [Serper 指南](../providers/serper.md)。
- Jev：安装官方 Skill：`npx skills add typesafe-ai/skills --skill typesafe-ai`，按照提示选择 Codex。该 Skill 不等于可调用的 Jev MCP；要实际执行由 TypeSafe SDK/API 或将来的 MarketOps 受管 MCP 完成。参见 [Jev 指南](../providers/jev.md)。

## 5. 本仓 Codex Plugin

[marketops-research-starter](../codex/marketops-research-starter/) 是插件目录候选，包含 TinyFish MCP 和轻量 Skill。支持导入插件的宿主可以加载它；如果当前 Codex 版本没有对应导入机制，使用本页官方 CLI 接入并手动安装 Skill。**尚无客户机安装回执。**

## 6. 实测

启动**新** Codex 会话后，先只让 Agent 用 Search 找到 3 个公开页面，再用 Fetch 读取其中一个，列出来源链接、访问时间、信息缺口。不自动登录、发邮件或使用付费 Agent。参见 [测试计划](../TEST_PLAN.md)。

参考：https://developers.openai.com/codex/config-reference 和 https://developers.openai.com/learn/docs-mcp 。


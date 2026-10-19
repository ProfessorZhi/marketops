# TinyFish — Search / Fetch / Browser Agent

**官方安装指南** https://github.com/tinyfish-io/tinyfish-cookbook/blob/main/INSTALL.md · **官方 MCP** `https://mcp.tinyfish.ai`。优先用 Search/Fetch 读取公开资料；只有必要、合法、经过明确授权时才考虑 `run_web_automation` 的多步网页操作。

## 两种认证：OAuth 或 API Key

**使用官方 MCP（建议非技术客户优先）**：

1. 使用 Codex 官方 TinyFish CLI 配置，或 WorkBuddy 远程 MCP 连接器。
2. 初次调用会触发 TinyFish OAuth，用户在浏览器中登录/授权。
3. 打开新客户端会话，核实 `search`、`fetch_content` 真正可调用（只看到已注册配置不算验证通过）。

**使用 API/SDK**：

1. 前往 https://agent.tinyfish.ai/ 注册/登录账号，按 Dashboard 生成自己的 `TINYFISH_API_KEY`。
2. 将 Key 保存在本地安全环境，而不是放入 Skills 或 Git。
3. 官方 REST/SDK 入口与用量说明见 https://github.com/tinyfish-io/tinyfish-cookbook 。Search/Fetch 的免费政策、Agent/Browser 的计价请以控制台实时信息为准。

## Codex 官方方式

    npx @tiny-fish/cli connect codex --dry-run
    npx @tiny-fish/cli connect codex
    codex mcp list

需要 Node.js / npx。dry-run 可以预览变更；确认后安装，检查 OAuth 和调用。遇到故障先执行 `npx @tiny-fish/cli doctor --pretty`，**不要自动运行 --fix**。

## WorkBuddy

使用 [候选 MCP 连接器](https://github.com/ProfessorZhi/marketops/tree/main/plugins/research-tools/workbuddy/tinyfish)（OAuth，待验证），或者在支持 HTTPS MCP 的 WorkBuddy UI 中手工填入 `https://mcp.tinyfish.ai`。不要把个人 API Key 复制给 MarketOps 管理员。

## 风险与停止条件

搜索/抓取失败不意味着可以自动升级到收费的 Browser Agent；升级前检查任务授权及实际价格。不自动登录、下单、提交表单、绕过验证码或更改第三方数据。

来源：https://github.com/tinyfish-io/tinyfish-cookbook/blob/main/INSTALL.md 。


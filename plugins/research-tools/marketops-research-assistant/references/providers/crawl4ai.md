# Crawl4AI — 网页抓取、批量提取

**官方项目** https://github.com/unclecode/crawl4ai · **Cloud API/MCP** https://api.crawl4ai.com/docs/ · **自托管** https://docs.crawl4ai.com/core/self-hosting/ 。适合把 URL 转成 Markdown、抽取 JSON、批量抓取公开站点。

## 申请 Cloud API Key

1. 打开 https://api.crawl4ai.com/，选择 **Get a key / Sign in**，按控制台步骤注册并验证账号。
2. 在 Cloud Dashboard 获取 Key（部分 Key 只展示一次）；妥善保存，只交给本机凭据库或本机安装的 agent，勿粘贴进 Git 或本机以外的任何地方。
3. 在控制台确认免费额度、余额、单次抓取/批量任务收费方式和账单设置；**不预设免费即无限额**。
4. 官方远程 MCP 为 `https://api.crawl4ai.com/mcp`，请求需要 `Authorization: Bearer <CLOUD_KEY>`；不要把真实值写到可提交的配置文件里。

## API 单页实验（会产生实际调用）

    curl -sS https://api.crawl4ai.com/scrape \
      -H "Authorization: Bearer $CRAWL4AI_API_KEY" \
      -H "Content-Type: application/json" \
      -d '{"url":"https://example.com","format":"md"}'

官方还提供 `/extract`、`/scrape/batch` 与长期运行 jobs；首次试用仅取已批准的小批公开网页，不要自动开启无限站点扫描。

## 客户端

- Codex：按 [Codex 指南](https://github.com/ProfessorZhi/marketops/blob/main/plugins/research-tools/clients/codex.md) 配置远程 MCP 的 Bearer 环境变量，或者按官方文档使用 HTTPS MCP。
- WorkBuddy：本仓的 [Crawl4AI 候选连接器](https://github.com/ProfessorZhi/marketops/tree/main/plugins/research-tools/workbuddy/crawl4ai) 使用 4.23.0+ 的用户自填 Token 模式，保持真实 Key 不进仓库；**尚未经过真实客户端认证验收**。
- 自托管：Crawl4AI 0.9.x Docker 服务器要求预置 `CRAWL4AI_API_TOKEN` 等安全配置，按官方自托管指南部署并隔离浏览器进程；**不属于本工具包的自动安装动作**。

## 风险

Cloud MCP 可能暴露 `topup`、`recharge`、`spend_cap` 等账单工具。测试者应使用 Provider 级限额和客户端限制；Skill 中写明禁止调用只是一层指导，不能当授权机制。验证码/登录墙不作为有效事实材料。

来源：https://api.crawl4ai.com/docs/ （MCP/mcp、API reference）与 https://docs.crawl4ai.com/core/self-hosting/。


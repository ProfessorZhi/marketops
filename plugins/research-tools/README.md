# MarketOps Research Tools | 半自动调研插件与工具包

> **试用候选 / 非生产发布（2026-10-15）**。本目录独立于 MarketOps 的业务服务、数据根和现有 `marketops-plugin`。只包含公开说明、无凭据配置样例、Skills 和 WorkBuddy 连接器候选；**未完成客户机上安装与真实付费端到端验收**。

## 目标

让已经使用 Codex / WorkBuddy / Claude Code 的团队先把半自动流程提速：**发现网站与场地 → 读取官网正文 → 处理少量交互页面 → 人工复核业务结论 → 导出带来源的候选清单**。不用先部署东京 Claude Code，也不用先接入完整 MarketOps 后端。

## 四种能力

| Provider | 推荐阶段 | 官方 / 非官方 | 本目录 |
| --- | --- | --- | --- |
| **Serper** | Google Search / Maps / Places，发现候选 URL | 官方 API；第三方 MCP 并非 Serper 官方维护 | [注册与使用](providers/serper.md) |
| **Crawl4AI** | 官网、PDF、批量网页、结构化抽取 | 官方 Cloud MCP / API；可自托管 | [申请 Key 与接入](providers/crawl4ai.md) |
| **TinyFish** | Search / Fetch；必要时执行多步网页交互 | 官方 MCP / CLI / Skill | [OAuth / API Key](providers/tinyfish.md) |
| **Jev / TypeSafe** | Choice / Noul / Score 结构化判断及不确定性 | 官方 SDK 与 Agent Skill；不宣称拥有已验收的官方 MCP | [申请 Key 与判断](providers/jev.md) |

## 选择入口

- **Codex**：先看 [Codex 安装与工具选择](clients/codex.md)，优先 TinyFish 官方连接器与本目录的研究 Skill，其他 Provider 按需开启。
- **WorkBuddy**：先看 [WorkBuddy 连接器指南](clients/workbuddy.md)；`workbuddy/tinyfish/` 与 `workbuddy/crawl4ai/` 是按开放平台规范整理的**候选包**，需在对应版本的真实客户端验收。
- **只用 API/SDK**：按四份 Provider 指南分别申请用户自己的凭据；API 调用样例仅为手动测试，不产生 MarketOps 数据库写入。

## 内容索引

- [SECURITY.md](SECURITY.md)：凭据、外网访问、付费工具、客户数据和隔离要求。
- [TEST_PLAN.md](TEST_PLAN.md)：静态校验、真人客户端连接、付费额度及研究质量验收。
- [research-lite Skill](skills/research-lite/SKILL.md)：跨供应商的半自动调查方法。
- [候选字段样例](examples/candidates.csv)：人工可审查的数据表；不可直接当作已核实的客户池。
- [Codex Plugin 候选](codex/marketops-research-starter/)：TinyFish MCP + Skill 的插件文件，未登录无法调用；不内置 Serper/Jev 密钥。

## 从手动研究到 MarketOps

当前版本**不启动任务、不开 Worker、不写东京数据库**。今后若连接正式 MarketOps Business/Research MCP，仍必须按经过批准的 Scene、Run、Evidence 和预算边界调用；搜索摘要只是发现线索，不是场地或联系人已经核实的证据。

## 资料可信度

官方入口：Serper https://serper.dev/ · Crawl4AI https://api.crawl4ai.com/docs/ · TinyFish https://github.com/tinyfish-io/tinyfish-cookbook · TypeSafe https://docs.typesafe.ai/introduction/quickstart · WorkBuddy https://open.workbuddy.cn/docs/connector · Codex https://developers.openai.com/learn/docs-mcp 。价格、配额和认证流程可能调整，应以各家控制台为准。


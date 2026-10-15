# Jev / TypeSafe — 有界判断与不确定性

**TypeSafe 官方** https://typesafe.ai/ · **官方 Quickstart** https://docs.typesafe.ai/introduction/quickstart · **官方 Agent Skill** https://github.com/typesafe-ai/skills 。Jev 返回 Choice、Score、Noul 等结构化问题答案及相关概率；不负责爬取网站、审批预算或执行外联。

## 申请 API Key

1. 先阅读 https://docs.typesafe.ai/introduction/quickstart 中的示例题面，确认确实需要独立语义判断。
2. 前往 TypeSafe Console https://console.typesafe.ai/ 注册/登录，在控制台按指引创建 API Key。
3. 设置用户自己的 `TYPESAFE_API_KEY`（本地安全环境变量或 Secret Store），检查当前模型名称、配额、计价与限制。
4. 不要把真实 Key 放在 Codex/WorkBuddy Skill 或 Git 中；Cloud 调用前明确告知可能产生费用。

## 安装官方 Skill

    npx skills add typesafe-ai/skills --skill typesafe-ai

根据命令提示选择 Codex 或兼容客户端。**Skill 是教 Agent 如何设计 Jev 判断的说明，不代表安装了可用的 Jev MCP**。是否支持 WorkBuddy 的 Skill 导入需要客户端验证。

## SDK 验证（仅供用户批准的单次调用）

    python3 -m venv .venv
    .venv/bin/pip install typesafe-sdk

随后按官方 Quickstart 用 `TypeSafeClient` 与 `Choice` / `Score` / `Noul` 建立小样本判断。不要猜造未经提供的概率阈值，结果必须保留原始概率并允许人工复核。

## MarketOps 关联

仓库已有 `JevDecisionProvider` SDK Adapter。未来正式运行的 Jev 决策由受管 Research MCP 调用并关联同一 Run/WorkItem；本半自动试用包不会直接写生产的 DecisionRecord。

## 第三方 MCP 的区别

独立网站宣称支持 Jev MCP 不等于 TypeSafe 官方维护，用户应单独审查其认证/数据处理。先使用官方 Skill/SDK 路线。

来源：https://docs.typesafe.ai/introduction/quickstart · https://github.com/typesafe-ai/skills 。


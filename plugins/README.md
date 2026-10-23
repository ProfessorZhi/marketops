# 仓库里的两个插件包

本仓库同时装着**两个**可安装的插件。它们不是新旧两代，也不是同一个产品的两个版本：
一个是**业务工作流 Skill**，一个是**研究能力包**。名字很像，装之前先看这张表。

| | `marketops-plugin/` | `plugins/research-tools/marketops-research-assistant/` |
|---|---|---|
| 插件名（`plugin.json` 的 `name`） | `marketops` | `marketops-research-assistant` |
| 当前版本 | `0.1.0` | `0.5.11-rc10`（release candidate） |
| 是什么 | 主 Skill：把「找市场 / 看 pool / 跟进 / 起草」这类请求路由到 **MarketOps 业务 MCP** | 研究能力包：一个自带的研究 MCP + 一组 research Skill |
| 自带 MCP 吗 | **不带**，必须连用户自己部署的业务服务 | **带**，装完即可本地起（stdio） |
| 装完立刻能用吗 | 不能，先要有业务 MCP 端点与令牌 | 能，配好供应商 key 就能跑 |
| 三端投影 | `marketops-plugin/client-projections/`（codex / workbuddy / muc-hosting，附 RECEIPT） | `docs/CLIENT_INSTALL.md` 指向的 `references/CUSTOMER_QUICKSTART.md` |

## 该装哪个

- **要跑一次市场研究**（搜来源、读页面、找联系人、出证据和台账）→ 装
  `marketops-research-assistant`。这也是仓库 [Release](https://github.com/ProfessorZhi/marketops/releases)
  发布、并且配了根 `.claude-plugin/marketplace.json` 的那一个：

  ```bash
  claude plugin marketplace add ProfessorZhi/marketops
  claude plugin install marketops-research-assistant@marketops
  ```

- **已经在跑 MarketOps 业务服务**，只想要那个会解释业务结果、把请求路由到服务的 Skill
  → 装 `marketops-plugin`。它不提供业务事实，也不自带连接；`client-projections/` 下的
  `status.json` 记录了每个端各自验证到哪一步（例如 Codex 的 MCP 连接与鉴权有回执，
  Skill 投影本身是 `not_verified`）。

## 两个包共有的边界

Skill 半边（流程文本，装在客户端 skills 目录）和连接半边（MCP 端点）**各自会失败，
不要互相冒充**。一边绿了不代表另一边绿了；安装回执里这两条要分开记。

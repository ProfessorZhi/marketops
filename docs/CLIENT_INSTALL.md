# 三端安装：Claude Code / Codex / WorkBuddy

本文**只是索引**，不重复命令。每个客户端的真实安装步骤只有一处权威来源，改命令时改那里，
这里只告诉你该读哪一段、以及**哪一端还没被验证过**。

包版本以 `plugins/research-tools/marketops-research-assistant/.claude-plugin/plugin.json`
为准；当前 `0.5.11-rc12`，是 release candidate，不是正式发行版。

## 一句话对照

研究插件的三端步骤都在
[`references/CUSTOMER_QUICKSTART.md`](../plugins/research-tools/marketops-research-assistant/references/CUSTOMER_QUICKSTART.md)
的「1. 安装源码包」与「4. 在宿主启用」两节；下表只指路。

| 客户端 | 安装 | 之后在客户端里 | 验证状态 |
|---|---|---|---|
| **Claude Code** | `Install.cmd -Client claude` / `install.sh <目录> claude` | 用安装器打印的 `claude --plugin-dir ...` 启动 | 有安装回执 |
| **Claude Code（市场版）** | `/plugin marketplace add ProfessorZhi/marketops`，再 `/plugin install marketops-research-assistant@marketops` | 还需要运行时：先跑一次上一行的安装器，或设 `MARKETOPS_RESEARCH_PYTHON` | Linux 上实测可起 MCP；**Windows 不可单独使用**（MCP 用 `sh` 启动） |
| **Codex** | `Install.cmd -Client codex`（`-RegisterCodex` 自动登记） | `codex plugin add marketops-research-assistant@marketops-local` | 有安装回执 |
| **WorkBuddy** | `Install.cmd -Client workbuddy` | 连接器管理 → 添加/导入自定义连接器 → 选安装器生成的本机目录 | **未验证**：还没在真实 WorkBuddy 上跑过，回执停在 `PENDING_HOST_IMPORT` |

`-Client both`（默认）= Codex + WorkBuddy，`-Client all` = 三端。插件没有运行时时，
Claude Code 会话一开始会说明缺什么、该运行哪个安装器。

`marketops-plugin/client-projections/` 下的 codex / workbuddy 文档属于**另一个包**
（业务 Skill，连用户自己部署的业务服务），不是研究插件的安装步骤，见
[`plugins/README.md`](../plugins/README.md)。

## 一条共同的边界

**Skill 半边和连接半边是两件事，各自会失败，不要互相冒充。**

- Skill 半边 = 插件里的流程文本，装在客户端的 skills 目录；
- 连接半边 = MCP 端点，由客户端自己的配置解析（Codex 是 `url` +
  `bearer_token_env_var`；WorkBuddy/Claude 用安装器生成的 MCP 配置）。

一边绿了不代表另一边绿了。安装回执里这两条要分开记。

## 安装前要准备的东西

| 项 | 说明 |
|---|---|
| Python 3.12 | 安装器和运行时都用它；Windows 无 Python 时安装器会尝试 winget |
| 账户 | Serper、SerpApi、TinyFish、TypeSafe（Jev）、DeepSeek；按任务启用，不必一次注册齐。Crawl4AI 本地运行，不需要账号 |
| 两个可选槽位 | `SERPER_API_KEY_2`、`SERPAPI_API_KEY_2`。**只在明确要换 key 时才切换，不自动轮换** |
| DeepSeek | 搜索与读页的一路（`DEEPSEEK_API_KEY`），$0.75 / 1k 次，失败不计费 |

Key 只进本机凭据库或环境变量，**不进包、不进仓库、不进日志**。
`research_credentials.py configure --stdin` 是写入入口，不要用命令行参数传值。

## 装完之后怎么确认

```bash
python scripts/preflight.py            # 依赖与凭据是否齐
python scripts/client_install_probe.py # 客户端配置是否落到它真正会读的位置
```

两个脚本都只报「哪一项缺」，不打印凭据值。研究质量、供应商认证和原生客户端验收是
**三份不同的报告**，不要用其中一份的通过去代替另一份。

## 已知的坑

- **Codex 的 `codex plugin add` 是拷贝**：源更新了它不会跟着变，必须重新 add。它没有
  auto-update 机制。
- **Claude Code 的版本号三处必须一致**：`plugin.json` 的 `version` 是权威，它没递增时
  `claude plugin update` 会判定「已是最新」并静默什么都不做。
- **WorkBuddy 的导入在客户端里完成**：安装器只写文件，导入动作不归它管；本机生成的连接器
  不能拿到另一台电脑导入。
- **市场版不带运行时**：`/plugin install` 只拷贝插件；没有运行时就没有 `marketops_*` 工具。
- **许可证是 MIT**（仓库根 `LICENSE`）。无许可证时公开仓库默认「保留所有权利」，
  装上也不能合法转发；本仓库已不再是那个状态。

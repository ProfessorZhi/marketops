---
name: marketops-setup
description: First-use configuration and install/preflight for the MarketOps Research Plugin in Codex or WorkBuddy — ask the two path questions once, then never again, without secretly activating paid APIs or changing existing customer credentials.
---

# Install, configure and preflight MarketOps Research

Read references/MEMBER_A_INSTALL.md first. Inspect OS/Python/installed client, installed package collisions and installation destination before changing files. Never overwrite existing setup or security settings automatically.

## 第一次使用：只问两个问题

首次在 Codex/WorkBuddy 启动统一 `marketops-research` 时，先调用只读工具
`marketops_setup_status`。它不改变任何东西，也不会创建文件。若返回
`CONFIG_REQUIRED`，就**问且只问**下面两句，尽量一次交互问完：

1. 研究数据要保存在哪个目录？（绝对路径）
2. 经验/卡点汇总文件要保存到哪个绝对路径，例如 `Research_Experience.md`？

拿到用户给的两个真实路径后，调用 `marketops_setup_configure(data_root, experience_file)`。
**不要假装路径已经存在，也不要替用户编一个默认目录**：未配置时 `marketops_setup_status`
里根本没有 `data_root`，插件也会拒绝把默认值兜回安装包——把调研数据默默写进插件目录
正是这条规则要防的事。

配置写入后（用户目录下一份带版本的 JSON，或 `MARKETOPS_RESEARCH_CONFIG` 指定的文件），
再次启动只显示已配置摘要（路径可能脱敏成 `~/...`），**不再提问**。要改路径必须拿到用户
明确指令，再带 `confirm=true` 调用；否则返回 `RECONFIGURE_REQUIRES_CONFIRMATION`，
旧路径保持不动。

约束（插件会拒绝，不必自己记住）：经验文件不能落在研究数据目录内部——它要能单独收取；
两个位置都不能在 Git 检出里；目标处已有一个**非** MarketOps 的同名文件时不会被覆盖。
Windows 与 Unix 的绝对路径都支持，不要写死任何一台机器的路径。

兼容性：显式 `MARKETOPS_RESEARCH_DATA_ROOT` 环境变量仍然优先于配置文件（现有测试/部署依赖它），
`MARKETOPS_RESEARCH_CONFIG` 可以指向另一份配置文件。优先级顺序是
环境变量 → 配置文件 → 结构化 `CONFIG_REQUIRED`。

## 五个上游服务与安装

The five upstream services are SerpApi, Serper, Crawl4AI, TinyFish and Jev/TypeSafe. Users may configure a subset. Missing provider access is UNAVAILABLE, not successful authentication. SerpApi and Serper require separate credentials. Crawl4AI can be local OSS or optional Cloud. LiteLLM or company model gateway does not replace the crawler; optional LLM extraction needs a distinct approved model API configuration.

Default networking is disabled for every provider. All provider call limits default to 0. Explain this before installing any package. If the user approves changing this computer, run:

python scripts/setup_clients.py --destination CUSTOMER-DIRECTORY --client codex|workbuddy|both --apply

Only add `--install-deps` after separately confirming that downloading Python packages is permitted. That flag does not create provider accounts. The installer stays conservative: nothing is downloaded and no marketplace is registered unless you ask for it explicitly. All keys remain with the customer OS or WorkBuddy credential form, never in source/Git/logs/chat.

For Codex: register the generated marketplace with the official Codex CLI and have the user enable plugin/Skills, then test MCP tool listing. For WorkBuddy: build the connector on the target machine after installing from source and import from its stable folder. An archive generated on a different machine is not a portable installed MCP.

Run a no-network capabilities check before requesting user approval for any live API calls. Full E2E can only be declared after a real search, acquisition, business candidate write/readback, Jev judgment if required, Excel output and event replay; all are not yet validated in the customer client.

Do not submit forms, send messages, subscribe to plans, top up credits or access secrets. Preserve user data outside Git.

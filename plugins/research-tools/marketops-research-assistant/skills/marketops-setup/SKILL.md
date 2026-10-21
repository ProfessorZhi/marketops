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

Only add `--install-deps` after separately confirming that downloading Python packages is permitted. That flag does not create provider accounts. The installer stays conservative: nothing is downloaded and no marketplace is registered unless you ask for it explicitly. All keys remain in the customer OS credential store, never in source/Git/logs or any system outside the user's machine. They arrive either through the installer's hidden prompt or as a JSON object on stdin to `research_credentials.py configure --stdin`.

For Codex: register the generated marketplace with the official Codex CLI and have the user enable plugin/Skills, then test MCP tool listing. For WorkBuddy: build the connector on the target machine after installing from source and import from its stable folder. An archive generated on a different machine is not a portable installed MCP.

Run a no-network capabilities check before requesting user approval for any live API calls. Full E2E can only be declared after a real search, acquisition, business candidate write/readback, Jev judgment if required, Excel output and event replay; all are not yet validated in the customer client.

Do not submit forms, send messages, subscribe to plans, top up credits or access secrets. Preserve user data outside Git.

## 五家可选 Provider：用户自己开通，不默认已登录

首次双路径配置完成后，调用 `marketops_setup_status` 读取 `provider_readiness`（各服务 READY 或需要的修复），按 [官方申请/API Key/MCP/登录态与费用指南](../../references/PROVIDER_ONBOARDING.md) 引导 Member-A **自行**决定启用哪些供应商。绝不要求一次填五个Key、不得自动注册、充值、下载或启用付费网络。每个项目的 provider 认证/配额/域名允许名单和TinyFish Browser Context Profile都需单独确认。

Claude Code 源码预览可用 `scripts/launch_research_mcp.sh`，缺 MCP SDK 就短码提示配置项目私有虚拟环境；安装程序生成可移植包时绑定绝对 venv Python。首用失败后须人工核对宿主 MCP连接状态和缓存，不得默默恢复为有权限的连接。

## 新：安装后的一站式账号引导

**必须先读** [中文客户初次使用指南](../../references/CUSTOMER_QUICKSTART.md)。用户安装完后，根目录会生成 `START_HERE.md`；安装包内有 `references/PROVIDER_CONNECTIONS.json`（TinyFish、SerpApi、Serper、Crawl4AI、Jev 的官方注册/连接模板）和 `scripts/provider_onboarding.py`（纯本地只读，打印SDK是否可导入及 Key 是否存在，**永不读取/输出 Key 值**）。

若用户明确批准安装并允许网络下载开源运行依赖，可在**目标计算机**用 `python3 scripts/setup_clients.py --destination 客户选定的全新绝对目录 --client all --apply --bootstrap-runtime` 一次性准备独立venv、受管MCP、Jev SDK、Crawl4AI SDK、Chromium并打包本机版WorkBuddy；下载依赖 ≠ 批准任何厂商收费调用。若只需要小体积精简版，可选 `--apply --install-deps` 而不装浏览器。**未经授权不得替用户运行 bootstrap、Docker 或官方厂商Plugin的安装命令。**

本地两路径配置仍然是**唯一强制问题**；之后按任务问用户要启用哪一家服务，不要求同时注册五个账号。TinyFish平台OAuth（官方MCP）与具体目标站点Browser Profile登录严格分离；SerpApi官方MCP由用户在宿主安全密钥设置里配置Bearer；Serper/TYPESAFE Key仅在需要时配置；Crawl4AI本地免费开源SDK无Cloud Key。**MarketOps内嵌的TinyFish/SerpApi官方MCP客户端已包含在程序中**，无须再在宿主分别安装这两家的插件；但其认证仍未配置/验证，需要用户在本机提供API Key或使用TinyFish内置OAuth登录命令，项目预算默认禁用。额外的宿主直连MCP仍不自动创建。付款/余额/网站权限缺失都必须明示，不自动升级为收费Agent。


TinyFish浏览器OAuth首次登录命令在 `INSTALL_RECEIPT.json.tinyfish_own_mcp_oauth_argv`；需要操作系统安全Keyring后端，成功也不代表目标网站Profile已登录。SerpApi只需要在本客户端安全环境输入 `SERPAPI_API_KEY`，不需再执行`claude mcp add serpapi`。优先按 [内嵌MCP说明](../../references/EMBEDDED_OFFICIAL_MCP.md) 的认证、费用、真实工具Schema做验收；不自动消耗收费额度。

# MarketOps 研究插件

统一入口：[MarketOps Research Assistant](marketops-research-assistant/README.md)。一个受管 MCP（18 个工具）、6 个按需加载的 Skill、两个 Claude Code 子代理和 `/research` 命令；六个项目（Serper、DeepSeek、SerpApi、Crawl4AI、TinyFish、Jev）都经同一 MCP 调用，证据、额度和数据集中在客户本机数据目录，交付审计过的 Excel。

同一插件适配 Claude Code（`.claude-plugin`）、Codex（`.codex-plugin`）和 WorkBuddy（安装器生成的连接包）。没有子代理的宿主按 Skill 中同一流程逐个机构执行。

TinyFish 和 SerpApi 官方 MCP 客户端直接随包。[账户指引](marketops-research-assistant/references/FOUR_ACCOUNT_START.md)说明 Serper、SerpApi、TinyFish、TypeSafe 的注册与 Key，以及可选的 DeepSeek；Crawl4AI 在本地运行。Windows 一键安装和 Linux 入口见 [START_HERE](marketops-research-assistant/START_HERE.md)。

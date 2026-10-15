# Codex / WorkBuddy / DSH 客户端配置（v0.4.0 Candidate）

**新版本唯一安装流程**：先读 [MEMBER_A_INSTALL.md](MEMBER_A_INSTALL.md) 再使用 skills/marketops-setup/SKILL.md 与 scripts/setup_clients.py。原有 v0.2/v0.3 的双搜索独立 MCP 和 8/15-Skills 指南已退居兼容用途。当前**只有一个** marketops-research Lite MCP 对接五家基础能力，不要同时安装旧 dual-search MCP 或另一个同名插件。

**Codex 配置审查：** 两份根目录 MCP JSON 均使用 `mcpServers`。Codex 曾有文档将兼容 `.mcp.json` 写成 `mcp_servers`，但实际加载器测试以 camelCase 为准；不要因看见旧文档就改为 snake_case。参考 [Codex 源码/问题记录](https://github.com/openai/codex/issues/22105)。WorkBuddy 同样要求 `mcpServers`。

## 安装步骤（由当前设备上的 Agent 辅助执行）

1. 只读检查本机：平台、Python >=3.11、Codex/WorkBuddy 版本、用户选择的目录、旧插件是否存在。**默认不下载、不更改现有环境，不运行付费查询。**
2. 先在插件源码目录执行：

~~~bash
python scripts/setup_clients.py --destination /CUSTOMER/NEW/INSTALL --client both
~~~

它只展示 dry run 安装计划。用户确认该台机器允许创建文件后用 --apply。用户再次确认 PyPI 依赖安装后才加 --install-deps。

3. Codex：脚本在安装根的 .agents/plugins/marketplace.json 创建来源，源码复制到 plugins/marketops-research-assistant，MCP 执行路径绑定本机 venv Python。由用户/Agent单独执行返回的 codex plugin marketplace add 命令，并在 Codex 插件界面启用。先做工具列举（零费用），再确认实际权限。官方规范：https://developers.openai.com/plugins/build/plugins 。

4. WorkBuddy：--client workbuddy|both 会在客户**当前机器**生成一个 connector-meta.json + mcp.json + token-schema.json + skills/ 目录，仅一个 marketops-research stdio MCP。WorkBuddy 官方规格：https://open.workbuddy.cn/docs/connector 。Token 表单由用户自己填写可选五家 Key、数据根与各预算/联网开关。需要 WorkBuddy >=4.24.0。**机器绑定的源码路径和Python路径不可拿到另一台电脑直接用。**

5. DSH：项目目前只有 Skills 文件兼容候选，没有已验收 Cordis 原生插件/自动安装。不可把 Codex/WorkBuddy PASS 迁移成 DSH PASS。

## 五家能力如何连接

| 上游 | 当前唯一市场研究 MCP 的路径 | 客户 Key |
| --- | --- | --- |
| SerpApi | local research_mcp -> dual_search_mcp -> SerpApi Google Web/Maps REST | SERPAPI_API_KEY |
| Serper.dev | local research_mcp -> dual_search_mcp -> Serper Google Web/Places REST | SERPER_API_KEY |
| Crawl4AI | local research_mcp -> AsyncWebCrawler OSS（可单独安装）或 Cloud HTTPS /scrape | 本地不需要第三方 Cloud Key；Cloud 需要 CRAWL4AI_API_KEY |
| TinyFish | local research_mcp -> 官方 Search/Fetch REST；交互浏览器 Agent 另接官方 MCP | TINYFISH_API_KEY 或官方 OAuth |
| Jev / TypeSafe | local research_mcp -> typesafe-sdk Choice；其它 Noul/Score 暂无MCP工具 | TYPESAFE_API_KEY |

五家都只实现了一部分**候选结果能力**，无正式 A/B/C/D 联系人晋升、完整业务数据库或验证通过的 E2E；用户也不必五家都申请才能开始一个小样本任务。

## 默认不联网与验收

用户设置 MARKETOPS_RESEARCH_DATA_ROOT（安装目录旁边独立 data/），**不能在 Git 仓库内部**。两套执行开关默认关闭，额度默认零：
- MARKETOPS_SEARCH_ALLOW_NETWORK=0，MARKETOPS_SEARCH_MAX_CALLS=0；
- MARKETOPS_RESEARCH_ALLOW_NETWORK=0，MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS=0。

只有用户批准本次查询及金额/次数后，才在本地宿主的凭据设置中配置并重新加载 MCP。Crawl4AI 本地可将浏览器算力放本机，但本地安装与联网抓取同样需要独立许可。实际网页目标必须属于 MARKETOPS_FETCH_ALLOWED_DOMAINS。

工具验收顺序：
- marketops_capabilities 不触发外部网络；
- marketops_start 创建带业务范围版本的本地 Run；
- marketops_search / crawl / tinyfish / jev 在各自获准后进行**一条**真实调用并读回；
- marketops_propose 仅写 candidate；
- marketops_status / marketops_events 可读回实际数据；
- marketops_handoff 同时生成候选 CSV 与未审批经验草稿，业务审核之后才能正式交付；
- 按 [RELEASE_GATE](RELEASE_GATE.md) 完成 Codex 与 WorkBuddy 两端的完整测试。

用户不需要提供完整聊天原始日志、公开客户列表或任何密码给 MarketOps 项目维护者。真实源网页、雇员联系方式等业务资料只保存在客户自行控制的受限数据根中。不要把本地 SQLite 经网络共享，不自动上传 GitHub。

## 卸载与回滚

从客户端管理界面移除 MarketOps 插件/连接器；如要删除安装根、venv或数据根，需要用户分别授权。不要修改并覆盖用户的全局 ~/.codex/config.toml 或 WorkBuddy 既有连接器配置。若本插件的测试版本发生迁移问题，保留既有数据并回退到上一个 Git SHA，禁止直接修改已交付的历史 Run。

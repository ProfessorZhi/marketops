# MarketOps 半自动调研插件：研究能力与安装入口

> **当前 v0.4.0 是 PR #64 Draft 候选；禁止以“装上即可完成正式联系人研究”名义发布。** 这是 MarketOps 的两份交付物之一；另一个是后续客户服务器上的完整自动化 CC 工作流。本目录专门实现 Member-A / Codex / WorkBuddy 的人工授权、Agent 执行的半自动研究能力。

## 当前唯一主入口

**[MarketOps Research Assistant](marketops-research-assistant/README.md)**

- **[MEMBER_A_INSTALL](marketops-research-assistant/references/MEMBER_A_INSTALL.md)**：给 Member-A 和 Agent 的安装/配置手册及可直接转发的提示。
- **[MCP_TOOL_CATALOG](marketops-research-assistant/references/MCP_TOOL_CATALOG.md)**：本地 Lite Research MCP 实际暴露的工具与未覆盖能力。
- **[BUSINESS_PLAYBOOK](marketops-research-assistant/references/BUSINESS_PLAYBOOK.md)**、**[MEMBER_A_SKILL_ANALYSIS](marketops-research-assistant/references/MEMBER_A_SKILL_ANALYSIS.md)**：Member-A Session 审计后固化的业务方法。
- **[STORAGE_AND_TOPOLOGY](marketops-research-assistant/references/STORAGE_AND_TOPOLOGY.md)**：五个项目的接入、开源/本地部署、客户数据根与事件库。
- **[RELEASE_GATE](marketops-research-assistant/references/RELEASE_GATE.md)**：发 ZIP 前必须签收的静态/离线/客户端/真实 E2E 验收。

## 当前统一插件结构

16 个 Skills：8 个业务、6 个 Provider/路由、1 个经验复盘、1 个安装配置。一个本地 `marketops-research` stdio MCP 对接：

| 上游 | 当前 Lite MCP 使用形式 | 边界 |
| --- | --- | --- |
| **SerpApi.com** | 官方 REST Google Web/Maps（自有Key） | 它不是 Serper；官方还有 MIT 开源MCP包装 |
| **Serper.dev** | 官方 REST Google Search/Places（自有Key） | 老 MarketOps 后端已有独立 Serper Adapter |
| **Crawl4AI** | 本机开源 AsyncWebCrawler 或官方 Cloud /scrape | 丰疆 LiteLLM 网关是可选抽取模型，**不是**爬虫服务 |
| **TinyFish** | 官方 Search/Fetch REST | 复杂浏览器 Agent 另接官方 MCP，尚未桥接本地事件 |
| **Jev / TypeSafe** | 官方 SDK Choice | Noul/Score尚未作为Lite MCP工具暴露 |

所有来源、场地、人员与组织资料只进入候选暂存库、衍生材料索引和操作日志，**不能无证据晋升正式联系人或自动发信**。本机 SQLite/CSV 是 Lite 验证模式，不替代 MarketOps 主项目的 Market Graph、SOP 分级与授权。

## 用户安装流程

源码解压之后交给客户 Agent 阅读 `MEMBER_A_INSTALL.md`、`skills/marketops-setup/SKILL.md`。先 dry-run 核对电脑/路径/依赖，再由用户逐步批准安装 Python venv、Codex 本地插件或 WorkBuddy MCP+Skills 连接器。安装器不自动注册五家付费账户、读取密钥或启动收费查询。

一个 Run 从客户需求和已有候选开始，经五家可选能力获取材料、更新候选场地/组织/岗位、保存证据、输出候选 CSV 与经验草稿。Excel Skill 在 Agent 真正具备 XLSX 工具时才能制作真实工作簿；没有时如实交 CSV。

## 保留的独立供应商参考

| Provider | 申请与安装 |
| --- | --- |
| SerpApi | [官方 MCP 与 Key](providers/serpapi.md) |
| Serper | [官方 API](providers/serper.md) |
| Crawl4AI | [本地/Cloud](providers/crawl4ai.md) |
| TinyFish | [官方 MCP/CLI](providers/tinyfish.md) |
| Jev/TypeSafe | [SDK/Key](providers/jev.md) |

`workbuddy/tinyfish/`、`workbuddy/crawl4ai/` 和 `codex/marketops-research-starter/` 是历史**独立候选**，不是当前 v0.4 统一插件。它们可能需要单独 OAuth/Key，也不会自动将调用回写我们的 Lite Research Store。切勿把多个直接连接的 MCP 当作同一个受管服务。

目前仅通过 GitHub 对 PR 文件、清单和路径范围进行了静态核对；Python 全套单元测试、Codex/WorkBuddy 真机安装、五个 Provider 真实调用、正式联系路线晋升和客户业务 E2E **均未宣布完成**。先做本地 E2E，再提供验证过、有SHA与安装说明的最终ZIP。

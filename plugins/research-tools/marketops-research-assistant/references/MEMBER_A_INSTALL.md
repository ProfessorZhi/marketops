# Member-A / Codex / WorkBuddy 安装与配置手册（v0.4.0 候选，尚未发布压缩包）

此插件位于 ProfessorZhi/marketops 内：
plugins/research-tools/marketops-research-assistant/

目标是半自动市场研究：Member-A 的需求与 SOP → 搜索来源 → 场地/组织与责任关系候选 → 相关岗位与公开联系路线候选 → Evidence → 人工审核 → CSV/Excel 阅读交付，并在整个过程记录真实工具操作和经验卡点。**当前只实现本机候选数据暂存，不是生产的正式联系人写入系统；五家全部的真实 E2E 还未验收。**

## 给 Member-A 的 Agent 直接复制的安装任务

> 请阅读本插件 references/MEMBER_A_INSTALL.md 与 skills/marketops-setup/SKILL.md。先审查代码和我的操作系统、Python、Codex/WorkBuddy 版本，列出计划和数据目录；未经我同意不要下载依赖、修改已有插件配置或联网调用付费 API。获得我确认后，把本插件安装在我的本机固定目录，使用 Python 虚拟环境，并为 Codex 注册本地 Marketplace，或为 WorkBuddy 生成 MCP+Skills 连接器。请不要要求我粘贴任何密钥到聊天，也不要打开充值或发信功能。配置完成先做不联网 preflight，再逐家展示需要我在自己设备上申请/填写的凭据和待测功能。最后如实报告哪些工具已可见、哪些只是配置就绪、哪些做过真实调用。

由 Agent 读取并执行上面的任务，比交付一个有开发机绝对路径的 ZIP 更安全。**在本地 E2E 通过前，不应给 Member-A 发送“可直接安装成功”的正式 ZIP**。

## 1. 各种组件的来源和客户要申请什么

| 上游 | 用户申请凭据 | 在本插件中的运行方式 | 限制 |
| --- | --- | --- | --- |
| SerpApi.com | https://serpapi.com/manage-api-key，SERPAPI_API_KEY | MarketOps MCP → SerpApi HTTPS REST；或另用官方托管 MCP | 多引擎、Places 候选，不产生验证事实 |
| Serper.dev | https://serper.dev/，SERPER_API_KEY | MarketOps MCP → Serper HTTPS API | 大批 Google 搜索候选，可与 SerpApi 分别试用 |
| Crawl4AI | 本地模式**不需第三方 Cloud Key**；Cloud 模式在 https://api.crawl4ai.com/ 申请 CRAWL4AI_API_KEY | MarketOps MCP → 本机 Crawl4AI Python/Chromium 或 Cloud /scrape | 非云模式需要另装浏览器依赖，不能由丰疆模型网关替代 |
| TinyFish | https://agent.tinyfish.ai/api-keys，TINYFISH_API_KEY | MarketOps MCP → 官方 Search/Fetch API；复杂交互另用官方 TinyFish MCP/OAuth | 本插件内浏览器 Agent 未桥接，不能宣称已完整提供 |
| Jev / TypeSafe | https://console.typesafe.ai/，TYPESAFE_API_KEY | MarketOps MCP → 官方 typesafe-sdk 的有界 Choice | Noul/Score 的完整工具封装尚未加入；绝不能覆盖 SOP |

密钥是客户自己的，且**没有任何一家必须在装包时就全部填好**。Provider 配置为可选；使用前需明确授权联网与额度。也不要求我们远程访问客户设备或东京 CC。

## 2. Crawl4AI：本地部署还是丰疆模型网关？

**推荐分层，不能二选一当成同一种服务：**

- Crawl4AI 是抓网页与渲染动态内容的程序，本地 Python + Chromium / Docker 是真正的采集执行端；数据留在本机/客户服务器。当前插件支持本地 AsyncWebCrawler 模式，但未在客户设备跑过安装测试。官方安装：https://docs.crawl4ai.com/core/installation/ 。
- Crawl4AI Cloud 提供托管抓取 API 和远程 MCP，Member-A 电脑不需要自行部署浏览器，适合首个 WorkBuddy 客户端小批试用，但需要在平台申请 Key、接受云端处理与额度控制：https://api.crawl4ai.com/docs/ 。
- **丰疆模型网关（LiteLLM）只可能是 Crawl4AI 的可选 LLM 文本提取/结构化理解后端，不能代替浏览器或抓取服务。** 其具体兼容模型名、Base URL、认证方式和授权仍需内部确认，不在客户压缩包里写死。若之后启用 LLMExtractionStrategy，官方 Crawl4AI 支持 LLMConfig 中指定 provider、api_token、base_url；应独立测试 OpenAI Chat Completions 兼容性、代理数据权限与费用，默认关闭。见 https://docs.crawl4ai.com/extraction/llm-strategies/ 。
- 对普通目录与职员网页，优先以无 LLM 的 CSS/XPath/正文提取保留字面来源；只有结构抽取确实需要语义判断且已批准成本，才使用丰疆网关。不要把抓不到资料诊断成“模型不够强”。

### 若选本地 Crawl4AI（明确授权后才安装）

在安装目录的独立 venv 中执行：

~~~bash
python -m pip install -r requirements-crawl-local.txt
crawl4ai-setup
crawl4ai-doctor
~~~

Crawl4AI 的浏览器依赖/Chromium 下载会产生文件和网络流量；Windows 企业终端权限可能有限。不能静默安装系统级浏览器或复用其他用户的 Chrome Profile。若选 Cloud 模式，不必安装本地 Crawl4AI，也不需要丰疆模型 Key。

在插件源码目录可先独立运行 `python scripts/preflight.py` 检查 Python 语法、16 Skills、两份 MCP 清单等静态结构。**它不联网、不检查 Key，也不代表真实服务可调用。**

## 3. 安装前核对：先计划再更改机器

Python 要求 >=3.11。客户需要有权下载并安装自己机器上的 Python 包；无网络权限时只能先做源码/Schema 检查。

在源插件目录运行：

~~~bash
python scripts/setup_clients.py --destination /ABSOLUTE/CUSTOMER/MarketOps-Research-v040 --client both
~~~

默认**只是 Dry Run**，没有写文件。路径必须是一个不存在或全空的客户目录，且不能位于 Git 源码树内部。

Member-A 明确批准创建文件和本机环境之后，再加 --apply；若批准下载 MCP/TypeSafe SDK 的依赖，再加 --install-deps：

~~~bash
python scripts/setup_clients.py --destination /ABSOLUTE/CUSTOMER/MarketOps-Research-v040 --client both --apply --install-deps
~~~

Windows PowerShell 也可以使用相同 Python 脚本，只需把路径换成客户自选的 Windows 绝对路径；不要照抄示例 Unix 路径。

安装程序做四件事：复制 Skills/源码 → 创建独立 .venv（不覆盖已有 venv） → 在 install root 下准备 Codex Marketplace → 在固定安装目录创建 WorkBuddy 的 1 个 Research MCP + Skills 候选。它不读取或保存 API Key、不给任何 Provider 充值、不发送外联、不替用户登录网站。

**安装不等于启用！** 纯源码复制、SDK 导入和 WorkBuddy Token 表单准备，都不是“通过真实 API 调用”证明。

## 4. Codex 本地启用

安装程序返回 codex_marketplace 与 codex_next 命令。用户/Agent 在本机检查该命令后执行，比如：

~~~bash
codex plugin marketplace add /ABSOLUTE/CUSTOMER/MarketOps-Research-v040
codex plugin marketplace list
~~~

在支持的 Codex/ChatGPT Desktop 插件面板中启用 marketops-research-assistant；重新启动会话后确认 Skill 与 marketops_capabilities 工具可见。现代插件采用根目录 plugin.json/mcp.json；.codex-plugin/plugin.json 和 .mcp.json 保留旧版兼容。安装程序会把本地 MCP 启动命令改成该客户机器专用的 venv Python，而不是依赖开发者 PATH。

若 Codex 客户端不支持本地 Plugin Marketplace，可由 Agent 经用户批准使用独立 MCP 注册命令并按宿主支持的 Skills 安装方式启用；需要如实报告“仅 MCP 已装/Skills 未生效”等部分状态。

## 5. WorkBuddy 本地启用

安装程序在客户机生成固定目录下的 connector-meta.json / mcp.json / token-schema.json / skills/ 等文件。请使用 WorkBuddy 4.24.0+ 的自定义连接器导入**本机生成的目录**，不要拿某个开发机预先生成的 WorkBuddy MCP ZIP 跨设备导入（其本地 Python 和脚本路径会失效）。

WorkBuddy 的连接器只能配一个 MCP Server，所以这个连接器把五家供应商封在 **一个 MarketOps Lite Research stdio MCP** 后面。用户自行在 WorkBuddy Token 模式界面输入可选的五个 Key、绝对数据根目录、允许域名、两类独立的联网开关及累计调用上限。表单一开始填 0（不联网/不扣费）。没有 Key 的供应商显示不可用，但整体 Skill 仍可学习业务方法。

详细 WorkBuddy 官方规格：https://open.workbuddy.cn/docs/connector 。只有真实导入、工具列表、单次试用与回读完成后才能写安装 PASS。

## 6. 统一 MCP 工具与本地数据区

客户自选环境变量 MARKETOPS_RESEARCH_DATA_ROOT。推荐放在**安装目录的数据卷/数据子目录**，不在源码插件目录。实际使用时，客户只需要把数据根传给当前 MCP 进程。

当前新增 Lite Research Store:
- state/research-lite.sqlite3：Run 与候选 venue/organization/people/contact/decision 等对象，**只有 candidate/hold，不是正式 CRM 或 MarketOps 生产 DB**；
- state/research-events.sqlite3：已接入工具的开始、完成、失败、保存事件；
- state/dual-search-ledger.sqlite3：两家搜索的累计调用预留；
- state/provider-reservations.sqlite3：Crawl4AI/TinyFish/Jev 的累计调用预留；
- artifacts/<run_id>/：实际获得的 JSON/Markdown/模型判断派生内容及 SHA；
- search-receipts/<run_id>/：SerpApi/Serper 搜索结果；
- exports/<run_id>/：经用户要求导出的候选 CSV；
- experience/<run_id>/：从实际事件生成的候选经验包；正式经验尚需回放和人工采纳。

工具目录见 references/MCP_TOOL_CATALOG.md。**不能让 agent 用一个“success”字段把候选自动晋升 A/B/C/D 正式联系人，或直接触发发信。**

## 7. 安全 preflight：零 Key、零付费调用

安装后由用户从 Codex 或 WorkBuddy 调用 marketops_capabilities，检查 five provider slots、SDK/凭据“已配置”标志和两类调用额度。所有联网开关仍为 0，不会调用任何第三方。

只有真实任务范围、法律合规/网页访问许可、必要的数据处理范围、目标域名、供应商额度已经由人批准，才开启对应能力。下载 SDK 也与“允许调用收费模型/外部浏览器”是两项独立授权。

## 8. 正式给 Member-A ZIP 之前必须过的 E2E

参照 references/RELEASE_GATE.md；先静态检查和 mocked 离线测试，再在本机运行 MCP tools/list、每个已启用能力的真实受控单次调用、资料保存/读回、Run 业务流、Excel/经验包和负向费用/访问测试，然后在 Codex 与 WorkBuddy **两端**安装验证。

此刻**不应**给任何人一个声称即装即用的压缩包。本文件是待测的安装与配置方法；验收后再从经验证的 Git commit 生成版本化源码 ZIP，并保存 SHA256、安装说明和回滚办法。

## 9. 相关官方资料

- Codex portable Plugins：https://developers.openai.com/plugins/build/plugins
- WorkBuddy Connector：https://open.workbuddy.cn/docs/connector
- SerpApi 官方 MCP：https://serpapi.com/integrations/mcp
- Serper 官网：https://serper.dev/
- Crawl4AI OSS 安装：https://docs.crawl4ai.com/core/installation/
- Crawl4AI Cloud：https://api.crawl4ai.com/docs/
- TinyFish 官方 INSTALL：https://github.com/tinyfish-io/tinyfish-cookbook/blob/main/INSTALL.md
- TypeSafe SDK：https://github.com/typesafe-ai/typesafe-sdk-python

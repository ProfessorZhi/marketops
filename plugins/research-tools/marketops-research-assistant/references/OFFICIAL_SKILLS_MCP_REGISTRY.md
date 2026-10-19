# 官方 Skill / MCP 按需接入登记（审核快照：2026-10-16）

**定位**：本页是 MarketOps 的外部厂商插件注册依据，**不是自动安装器，不是账号授权证明，也不是五家全部接口已经能调用的声明**。现有17项内部Skills + 23项MarketOps MCP工具；TinyFish和SerpApi官方HTTP MCP客户端**已内嵌**，无需在宿主单独安装厂商插件；用户仍须在本插件向导完成Key/OAuth和项目费用批准。其他额外的宿主直连插件不是默认要求。不要为了“装全”把所有官方 Skill 一次载入模型上下文。

## 已核实的官方来源

| 厂商 | 核实的上游高质量资产 | 精确来源（GitHub blob SHA，核对时） | 推荐接入 |
|---|---|---|---|
| TinyFish | **官方 `use-tinyfish` Skill** 与官方 Claude Code 插件／OAuth MCP | [Skill](https://github.com/tinyfish-io/tinyfish-cookbook/blob/main/skills/use-tinyfish/SKILL.md) · `5b769d783895732d61caccf03cd24ee52761a51a`；[INSTALL](https://github.com/tinyfish-io/tinyfish-cookbook/blob/main/INSTALL.md) · `f182c56d7761d3a2265d092d6c461fd8868e48e1` | MarketOps内嵌真实MCP Client；官方Skill仅供按需参考，单独宿主插件可选但不默认 |
| SerpApi | **官方 `serpapi-web-search` Skill** 与原厂 MCP | [Skill](https://github.com/serpapi/skills/blob/master/skills/serpapi-web-search/SKILL.md) · `ad19b037182343bad4b75e612453d7936f1fdbd3`；[MCP](https://github.com/serpapi/serpapi-mcp) README · `935d26192b3cf2754fe9f3b604ec5c3a30276757` | 广泛多引擎搜索由官方单一 `search` MCP；简单 Google 分类优先 Lite 受管 API |
| Crawl4AI | **官方自托管 Docker+MCP 指南** | [self-hosting](https://github.com/unclecode/crawl4ai/blob/main/docs/md_v2/core/self-hosting.md) · `5f6ae1c4106f6cac6de3b79f9c8d5baff3e53767` | 单机优先SDK；确有共享浏览器池需求才部署自托管MCP，Cloud另行认证 |
| TypeSafe Jev | **官方 `typesafe-ai` Skill**，配合SDK/API live docs | [Skill](https://github.com/typesafe-ai/skills/blob/main/skills/typesafe-ai/SKILL.md) · `0109513f9656917dc93cbc5ecddfca465a53ce66` | 当前Lite的Choice/Noul/Score SDK统一工具，复杂组合按需参考官方Skill和实时文档 |
| Serper | 官方 REST API 和各类 Google 搜索端点 | [官网](https://serper.dev/) | 没有核实到厂商自己维护的官方MCP/Skill；现阶段使用MarketOps `marketops-serper`，不能把社区版标成官方 |

MarketOps实际嵌入细节见 [EMBEDDED_OFFICIAL_MCP](EMBEDDED_OFFICIAL_MCP.md)。账号持有人可运行本插件提供的 `scripts/research_official_mcp_auth.py login --provider tinyfish`，并由OS Keyring保管Token；SerpApi由本插件的Bearer Key HTTPS Client直接连接。下面原厂安装命令仅给**明确想额外直接用厂商宿主插件**的人参考，别在安装MarketOps时执行两遍。

GitHub blob SHA 是**取回的单文件内容指纹**，不是安装确认，也不是整个仓库 commit pin。安装前应复核上游当前版本、依赖与供应链权限；不要复制第三方Skill全文并覆盖 MarketOps 的路由合同。

## 可选宿主直连安装（**不是**使用MarketOps内嵌MCP所必需）

1. **TinyFish 官方 Claude Code**：人工选择安装后可执行 `/plugin marketplace add tinyfish-io/tinyfish-cookbook` 和 `/plugin install tinyfish@tinyfish-marketplace`。之后用户在 `/mcp` 选择 TinyFish 并完成浏览器 OAuth，**启动新会话**才会看见该官方MCP工具。官方还提供 `npx @tiny-fish/cli connect codex` 等适配命令，执行前先看 `--dry-run`。登录到 *TinyFish 平台* 与登录到 *目标网站的 Browser Context Profile* 是两件事；后者必须由账号持有人操作。高阶 Agent／Browser 分别依项目付费批准，不能因为官方 Skill 原文说“所有网页都用TinyFish”就覆盖本插件的成本先行原则。
2. **SerpApi 官方 MCP**：推荐不含Key的固定URL `https://mcp.serpapi.com/mcp`，由**客户端受保护的 Authorization: Bearer** 配置传入 Key。Codex 官方README示例 `codex mcp add serpapi --url https://mcp.serpapi.com/mcp --bearer-token-env-var SERPAPI_API_KEY`；Claude Code有 header 配置方式。绝不采用把 API Key 放进 URL 路径的复制/日志方案。原厂 Skill 文档也使用 `SERPAPI_KEY`，与 MarketOps Lite 的 `SERPAPI_API_KEY` 名称不同，配置时必须由用户明确匹配，不能悄悄读取另一家或另一个工作区的 Key。
3. **Crawl4AI 自托管**：官方0.9.x指南：Docker 版 **必须给 `CRAWL4AI_API_TOKEN`** 才能正确对宿主暴露服务；它与Cloud的 `CRAWL4AI_API_KEY` 不同。自托管运行本身会占用机器资源。是否启动 Docker/服务、公开端口和授权登录均需要用户批准，不应由 Skill 自动部署。Cloud MCP 用云账户密钥和独立额度。
4. **TypeSafe Jev**：官方高质量 Skill 的知识增益在 **state/question设计、Choice/Noul/Score分布的正确解释、组合问题及证据核验**；Lite当前只有逐项受管请求。必要时定向读取官方Skill与 live docs；绝不把“有官方Skill”当成 SDK已安装/有额度/业务正确。
5. **Serper**：使用已受管的 Lite REST Google 搜索；如另行引入社区MCP，须先核对维护者、许可证、请求透传的密钥与日志、实际的接口面，并确认不会绕过已有的预算与证据持久化。

## 接入后怎样确认“真的可用”

- `DOCS_REFERENCED`：本页有上游来源；**不意味着可调用**。
- `HOST_INSTALLED`：客户端工具发现，实际schema已读；**不意味着鉴权成功**。
- `AUTH_READY`：用户完成OAuth/Key，取得真实连接结果；**不意味着余额和访问某站已获授权**。
- `WALLET_AND_QUOTA_CHECKED`：只读获取该账户当前计费与限额并确认新鲜；**不意味着本次任务自动获准付费**。
- `PROJECT_ALLOWED`：这一次 Run 的域名、动作、最大步数/预算、登录账户及只读/写权限经过明确允许；官方外部 MCP **不自动进入 Lite SQLite 硬预算**。
- `EXERCISED`：真正调用并保存实际回执、材料与来源；**不意味着业务结论已获人工/SOP确认**。

默认（无权限、余额未知、Key缺失或国外站点登录态失效）一律保留已采集事实，使用另外**合法、已授权且不增加未经批准费用**的来源/本地方法；不可通过更换MCP来绕过Cloudflare/CAPTCHA、付款墙或授权。

## 避免Skill增殖

本插件保留 17 个内部Skills：`marketops-provider-router` 才知道完整任务上下文/预算，厂商Skill仅在本阶段被选中时加载。对 TinyFish `use-tinyfish` 等官方Skill采取**引用/可选用户安装**，不默认安装其全局强触发，否则它可能将普通网页请求直接升级为Agent，增加Token和资金浪费。优先消费官方MCP中**有限、结构化、低token的工具响应**，并经MarketOps存储/证据门槛统一审计；禁止将外部官方工具的大型输出直接累积在Agent历史消息中。

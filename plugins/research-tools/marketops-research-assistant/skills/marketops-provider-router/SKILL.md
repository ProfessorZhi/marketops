---
name: marketops-provider-router
description: Select one actually available, authorized provider capability for a concrete current-stage gap; load only that provider's Skill and preserve budget, source lineage and failures.
---

# Provider router — 按需加载

**触发：** 当前 Run 存在具体外部数据/提取/语义判断缺口。输入是 Brief、marketops_next 的 missing_facts、已存材料和允许费用，不是整个工具库。

## 已授权范围内的有界降级

需要自动选择后备时调用 `marketops_research_step(run_id, operation_id, task_kind, ..., max_attempts=3)`；
每条链最多 3 次受管调用，所有供应商仍由本机配置、权限和同一费用账本逐次核验。先看已有材料与经验，
不要把缺少授权当失败后自动放宽。`operation_id` 标识这一次逻辑步骤，重试/恢复沿用它，已完成回执直接复用。

| task_kind | 降级顺序 | 必要输入 |
| --- | --- | --- |
| `source_discovery` | Serper → SerpApi → TinyFish Search | query、Brief 的 country/language，不猜 US |
| `known_public_url` / `batch_public_pages` | Crawl4AI → TinyFish Fetch | 当前一个 url；批次逐页使用独立步骤 |
| `semantic_choice` | Jev Choice → 原文/人工复核 HOLD | 已保存 artifact_id、question、options |

仅未配置/未获准的供应商可跳过；明确普通技术失败或有回执的空材料才换下一家。403/401/429、WAF、
CAPTCHA、域名/出口政策拒绝立即停止该来源，不能换工具绕过。TIMEOUT、UNKNOWN、调用中断或不能分类的
错误停止整链，保留可能费用；不重新试同一家、不重置预算、不升级 Browser/Agent/T3。某家额度耗尽可以
检查另一家已有许可，共享项目/Run/单操作额度耗尽则全部停止。已知终态失败但美元费用未知可在已有有限
请求许可内换另一家，未知金额必须继续列出，不能报免费。

返回 `SUCCESS_CANDIDATE` 只表示获得实际已保存材料/来源候选；继续逐字段证据审查。`HOLD` 显示每次尝试、
parent_ref、子 operation_id、原因及未知费用；不要通过更换父 ID 强行重跑。读取 `artifact_ids` 对应材料；
Jev 不可用时不得编造替代判断。链的最后观察自动进经验记录，业务正确性和提效仍须实际验收。

TinyFish Fetch 后备请求 HTML：优先读取 `provider_html_artifact_ids` 的逐 URL 材料，保留供应商信封作审计。
这类 `provider_html` 是供应商清理后的语义 HTML，可作字面引文，**不是源站 HTTP 原件或浏览器原始 DOM**；
不要改标 raw/rendered，不从 Markdown 信封推定原始邮箱证据。Crawl4AI 的原材料仍在 `primary_artifact_ids`。

1. 先复用适用的已存材料。调用统一 MCP 的 `marketops_capabilities(task_kind=source_discovery|known_public_url|batch_public_pages|dynamic_navigation|authorized_login_read|semantic_choice|semantic_yes_no|semantic_score)`；配置、SDK安装、认证和真实调用成功是四种不同状态。首次选择 Crawl4AI local 时先看 `browser_cache_hint`：`CHROMIUM_CACHE_MISSING_OR_INCOMPLETE` 表示先修本机安装/HOME隔离路径，不要连续预订十几次失败请求；`CHROMIUM_CACHE_PRESENT_NOT_LAUNCH_VERIFIED` 只是线索，不是浏览器已能启动的证明。
2. 未知URL：优先按预算选择已配置的 `marketops_serpapi_mcp_search`、Serper `marketops_search` 或 `marketops_tinyfish(task='search',transport='mcp')`。有URL：获批Crawl4AI local/cloud或TinyFish fetch。已有证据的语义辅助：Jev Choice。去重/计数/硬规则不交给模型判断。
3. 选择后只加载相应 marketops-serpapi / marketops-serper / marketops-crawl4ai / marketops-tinyfish / marketops-jev Skill，细节才读 [Provider路由](../../references/PROVIDER_ROUTING.md)；确实要安装/调用官方扩展时，再读 [已核验官方Skill/MCP](../../references/OFFICIAL_SKILLS_MCP_REGISTRY.md)。不要一次加载所有Provider文档。
4. **真实工具边界**：MarketOps自身已内嵌官方TinyFish/SerpApi HTTP MCP Client，不要求用户另安装厂商宿主MCP。TinyFish `marketops_tinyfish(task=search|fetch,transport=mcp)` 默认走内嵌官方MCP；付费 `marketops_tinyfish_agent` 独立强制上限+钱包认证，原Agent只通过 `marketops_tinyfish_agent_wait` 查询，不会静默再提交。SerpApi的任意允许引擎使用 `marketops_serpapi_mcp_search(params={...})`；旧MarketOps SerpApi REST仅兼容显式选择。Jev仍以一个 `marketops_jev_choice(primitive='choice'|'noul'|'score')` 工具运行三个原语。**宿主内置免费WebSearch/WebFetch和外部直连官方MCP不在本地自动调用账本/去重链内**，不得因受管账本显示无重复就声称所有搜索都没有重复。已实际使用时只记录脱敏的操作ID、来源类型、获得的材料和失败状态，不复制查询全文或原始页面；避免用直连工具反复绕过同一受限站点。
5. 请求前明确provider、operation_id、目标、边界、调用上限；没有批准不自动收费，不把next建议当成授权。返回后确认真实材料ID、候选写入与readback，不只报告“工具跑了”。
6. 区分网络、权限墙、提取、存储和业务缺口。已接受job但本地超时，先查原job，不能默认未计费或重复创建。不得用换工具绕过同一访问限制，也不得静默切到另一家收费Provider。
7. **逐功能成本感知**：TinyFish官方MCP中的 `search`/`fetch_content` 与另行按step计费的 `run_web_automation` 是独立能力；某项目浏览器钱包不足不代表免费Fetch不能用。先经官方只读钱包/账户、项目允许列表核对实际情况；`marketops_capabilities` 的host工具名字来自宿主回报，是线索不是OAuth、额度或账户认证。缺少本机有限授权时 HOLD；已有显式 REQUEST_COUNT 许可的 T1/T2 可按上述有界链运行，美元余额未知仍保持未知。付费Agent/Browser/Cloud不属于自动后备。具体安装、账户/浏览器Profile分离详见 [Provider入门与多处鉴权](../../references/PROVIDER_ONBOARDING.md)。
8. **去重与访问墙：** 同 Run 同一请求指纹即使更换 operation_id 也不得自动重试。遇 `SOURCE_CHALLENGE_PAGE` / `SOURCE_ACCESS_BLOCKED`（包含 Cloudflare/验证码/403/429）停止本 Run 同站点进一步抓取；不切换代理、账号、浏览器伪装或另一家 Provider 去绕挑战。先查已保存材料、公开开放数据/合法可读的官方文件或**不同机构**的公开来源；仍无证据则 HOLD 并注明未访问。网络失败只代表访问未知，不是没有客户。环境缺浏览器先诊断并修复路径，但不能换 ID 盲重试，必要时另行批准与记录。
9. 目标已完成、方法明确阻塞或额度耗尽就停止该方法；已授权的独立对象可以继续。卡点/换路/修正通过marketops_note留下观察、动作和结果，假设不冒充根因。

**输出/退出：** 实际来源/材料/判断引用和持久化处置，返回调用方阶段Skill；阶段是否可推进由marketops_next的结构检查和后续证据验收共同决定。统一数据合同见 [WORKFLOW_CONTRACT](../../references/WORKFLOW_CONTRACT.md)。

**调用透明度**：每次受管 Provider 结果的 `invocation` 说明外层 MarketOps MCP 与内层 REST/SDK 的区别、所用Key方式和实际计费单位；`actual_amount=null` 表示未取得厂商账单，不能猜金额。若用户问本次调用了什么，读取 `marketops_events(run_id)` 最近20条 + `latest_lite_provider_call`；**独立官方MCP调用不在Lite账本内**，需Host单独披露，详情见 [调用通道与费用回执](../../references/PROVIDER_TRANSPORT_BILLING.md)。

**准确工具映射（先看 `marketops_capabilities`，只在当前任务命中后加载对应 Skill）：**

| 选择的业务能力 | 当前 MarketOps MCP 的确定性入口 | 真实执行路径 |
| --- | --- | --- |
| Serper搜索 | `marketops_search(provider='serper', ...)` | HTTPS REST，Serper API Key |
| SerpApi搜索 | `marketops_serpapi_mcp_search(run_id,operation_id,params)` | **内嵌官方HTTP MCP Client**，通用引擎 + SerpApi API Key Bearer；旧REST明确选择才用 |
| TinyFish轻量搜索/取文 | `marketops_tinyfish(task='search'|'fetch',transport='mcp',...)` | **内嵌官方HTTP MCP Client**，API Key或OS Keyring OAuth；`transport='rest'`仅兼容 |
| Crawl4AI抓取 | `marketops_crawl(implementation='local'|'cloud', ...)` | 本地SDK无需厂商Key；Cloud REST另需Cloud Key和费用许可 |
| Jev结构化语义判断 | `marketops_jev_choice(primitive='choice'|'noul'|'score', ...)` | TypeSafe Python SDK + API Key；输出不等于来源证据 |

不得因官方厂商 Skill 在参考登记中存在，就把它当成已安装、OAuth已完成或费用已获批。

**高级额外能力**：`marketops_tinyfish_agent` 是官方MCP Agent的真实受管提交（强制域名/钱包/费率/硬步数/时长/预算门槛），`marketops_tinyfish_agent_wait` 只查本Run同一已提交任务；客户如额外独立安装厂商宿主插件并直接调用，仍不属于Lite账本。详情见 [内嵌官方MCP桥](../../references/EMBEDDED_OFFICIAL_MCP.md)。

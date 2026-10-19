---
name: marketops-provider-router
description: Select one actually available, authorized provider capability for a concrete current-stage gap; load only that provider's Skill and preserve budget, source lineage and failures.
---

# Provider router — 按需加载

**触发：** 当前 Run 存在具体外部数据/提取/语义判断缺口。输入是 Brief、marketops_next 的 missing_facts、已存材料和允许费用，不是整个工具库。

1. 先复用适用的已存材料。调用统一 MCP 的 `marketops_capabilities(task_kind=source_discovery|known_public_url|batch_public_pages|dynamic_navigation|authorized_login_read|semantic_choice|semantic_yes_no|semantic_score)`；配置、SDK安装、认证和真实调用成功是四种不同状态。首次选择 Crawl4AI local 时先看 `browser_cache_hint`：`CHROMIUM_CACHE_MISSING_OR_INCOMPLETE` 表示先修本机安装/HOME隔离路径，不要连续预订十几次失败请求；`CHROMIUM_CACHE_PRESENT_NOT_LAUNCH_VERIFIED` 只是线索，不是浏览器已能启动的证明。
2. 未知URL：优先按预算选择已配置的 `marketops_serpapi_mcp_search`、Serper `marketops_search` 或 `marketops_tinyfish(task='search',transport='mcp')`。有URL：获批Crawl4AI local/cloud或TinyFish fetch。已有证据的语义辅助：Jev Choice。去重/计数/硬规则不交给模型判断。
3. 选择后只加载相应 marketops-serpapi / marketops-serper / marketops-crawl4ai / marketops-tinyfish / marketops-jev Skill，细节才读 [Provider路由](../../references/PROVIDER_ROUTING.md)；确实要安装/调用官方扩展时，再读 [已核验官方Skill/MCP](../../references/OFFICIAL_SKILLS_MCP_REGISTRY.md)。不要一次加载所有Provider文档。
4. **真实工具边界**：MarketOps自身已内嵌官方TinyFish/SerpApi HTTP MCP Client，不要求用户另安装厂商宿主MCP。TinyFish `marketops_tinyfish(task=search|fetch,transport=mcp)` 默认走内嵌官方MCP；付费 `marketops_tinyfish_agent` 独立强制上限+钱包认证，原Agent只通过 `marketops_tinyfish_agent_wait` 查询，不会静默再提交。SerpApi的任意允许引擎使用 `marketops_serpapi_mcp_search(params={...})`；旧MarketOps SerpApi REST仅兼容显式选择。Jev仍以一个 `marketops_jev_choice(primitive='choice'|'noul'|'score')` 工具运行三个原语。**宿主内置免费WebSearch/WebFetch和外部直连官方MCP不在本地自动调用账本/去重链内**，不得因受管账本显示无重复就声称所有搜索都没有重复。已实际使用时只记录脱敏的操作ID、来源类型、获得的材料和失败状态，不复制查询全文或原始页面；避免用直连工具反复绕过同一受限站点。
5. 请求前明确provider、operation_id、目标、边界、调用上限；没有批准不自动收费，不把next建议当成授权。返回后确认真实材料ID、候选写入与readback，不只报告“工具跑了”。
6. 区分网络、权限墙、提取、存储和业务缺口。已接受job但本地超时，先查原job，不能默认未计费或重复创建。不得用换工具绕过同一访问限制，也不得静默切到另一家收费Provider。
7. **逐功能成本感知**：TinyFish官方MCP中的 `search`/`fetch_content` 与另行按step计费的 `run_web_automation` 是独立能力；某项目浏览器钱包不足不代表免费Fetch不能用。先经官方只读钱包/账户、项目允许列表核对实际情况；`marketops_capabilities` 的host工具名字来自宿主回报，是线索不是OAuth、额度或账户认证。付费Agent/Browser/Crawl4AI Cloud/SerpApi/Serper/Jev不得自动切换，预算未知即HOLD/提示。具体安装、账户/浏览器Profile分离详见 [Provider入门与多处鉴权](../../references/PROVIDER_ONBOARDING.md)。
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
